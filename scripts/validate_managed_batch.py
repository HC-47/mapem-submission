from __future__ import annotations

import argparse
import json
import re
import stat
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from validate_submission import (
    CONTRACTS,
    MAX_REPORTED_ERRORS,
    REQUIRED_FILES,
    git,
    read_json,
    read_yaml,
    sha256_file,
    validate_observations,
    validate_schema,
    verify_pinned_contracts,
)


ROOT = Path(__file__).resolve().parents[1]
BATCH_FILE = "batch.yaml"
BATCH_PATH = re.compile(
    r"^managed-batches/(?P<batch>[a-z0-9]+(?:[a-z0-9._-]*[a-z0-9])?)/(?P<rest>.+)$"
)


@dataclass
class ManagedBatchReport:
    valid: bool = False
    pull_request: int | None = None
    head_sha: str | None = None
    batch_id: str | None = None
    batch_path: str | None = None
    datasets: int = 0
    observations: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    manifest_sha256: str | None = None
    artifact_sha256: dict[str, dict[str, str]] = field(default_factory=dict)


def managed_limits() -> dict[str, Any]:
    return read_yaml(CONTRACTS / "governance.yaml")["managed_batches"]


def resolve_pr_batch(
    base_sha: str,
    head_sha: str,
    errors: list[str],
    repository: Path = ROOT,
) -> Path | None:
    changed = [
        line.split("\t")
        for line in git(
            repository, "diff", "--name-status", "--no-renames", base_sha, head_sha, "--"
        ).splitlines()
        if line
    ]
    if not changed:
        errors.append("pull request contains no changed files")
        return None
    roots: set[str] = set()
    for parts in changed:
        status_code, *paths = parts
        path = paths[-1] if paths else ""
        match = BATCH_PATH.fullmatch(path)
        if status_code != "A":
            errors.append(f"{path}: managed batches are immutable; only added files are allowed")
            continue
        if match is None:
            errors.append(f"{path}: a managed PR may only add files under one batch directory")
            continue
        roots.add(f"managed-batches/{match['batch']}")
    if len(roots) != 1:
        errors.append("a managed pull request must add exactly one batch directory")
        return None
    candidate = (repository / next(iter(roots))).resolve()
    managed_root = (repository / "managed-batches").resolve()
    try:
        relative = candidate.relative_to(managed_root)
    except ValueError:
        errors.append("managed batch path escapes managed-batches")
        return None
    if len(relative.parts) != 1:
        errors.append("managed batch path must have exactly one batch-id component")
        return None
    return candidate


def _check_files(
    directory: Path,
    expected: set[str],
    errors: list[str],
) -> None:
    limits = managed_limits()
    actual_paths = [item for item in directory.rglob("*") if not item.is_dir()]
    actual = {item.relative_to(directory).as_posix() for item in actual_paths}
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing:
        errors.append("batch is missing files: " + ", ".join(missing))
    if extra:
        errors.append("batch contains undeclared files: " + ", ".join(extra))
    total_bytes = 0
    for path in actual_paths:
        if path.is_symlink():
            errors.append(f"{path.name}: symlinks are not allowed")
            continue
        mode = path.stat().st_mode
        if not stat.S_ISREG(mode):
            errors.append(f"{path.name}: must be a regular file")
        if mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
            errors.append(f"{path.name}: executable files are not allowed")
        total_bytes += path.stat().st_size
        with path.open("rb") as stream:
            if b"\x00" in stream.read(8192):
                errors.append(f"{path.name}: binary content is not allowed")
    if total_bytes > int(limits["maximum_total_bytes"]):
        errors.append("batch exceeds the configured total size limit")


def validate_managed_batch(directory: Path, report: ManagedBatchReport) -> None:
    manifest_path = directory / BATCH_FILE
    if not manifest_path.is_file():
        report.errors.append("batch.yaml is required")
        return
    try:
        manifest = read_yaml(manifest_path)
    except Exception as error:
        report.errors.append(f"unable to parse batch.yaml: {error}")
        return
    validate_schema(
        manifest,
        CONTRACTS / "managed-batch.schema.json",
        "batch.yaml",
        report.errors,
    )
    batch_id = directory.name
    report.batch_id = batch_id
    report.batch_path = f"managed-batches/{batch_id}"
    report.manifest_sha256 = sha256_file(manifest_path)
    if manifest.get("batch_id") != batch_id:
        report.errors.append("batch.yaml: batch_id must match the directory name")
    limits = managed_limits()
    if manifest.get("provider") != limits["provider"]:
        report.errors.append("batch.yaml: provider is not allowed for managed intake")
    if manifest.get("channel") != limits["channel"]:
        report.errors.append("batch.yaml: channel must be managed_public")
    if manifest.get("source_config_sha256") != sha256_file(
        CONTRACTS / "eurostat-datasets.json"
    ):
        report.errors.append("batch.yaml: Eurostat source configuration is not the pinned contract")

    configs = {
        item["id"]: item for item in read_json(CONTRACTS / "eurostat-datasets.json")
    }
    entries = manifest.get("datasets", [])
    if not isinstance(entries, list):
        entries = []
    if len(entries) > int(limits["maximum_datasets"]):
        report.errors.append("batch exceeds the configured dataset limit")
    dataset_ids = [item.get("dataset_id") for item in entries if isinstance(item, dict)]
    if len(dataset_ids) != len(set(dataset_ids)):
        report.errors.append("batch.yaml: dataset_id values must be unique")
    unchanged = manifest.get("unchanged_datasets", [])
    if set(dataset_ids) & set(unchanged if isinstance(unchanged, list) else []):
        report.errors.append("batch.yaml: a dataset cannot be both changed and unchanged")

    expected = {BATCH_FILE}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        relative_release = f"datasets/{entry.get('dataset_id')}/{entry.get('version')}"
        expected.update(f"{relative_release}/{filename}" for filename in REQUIRED_FILES)
    _check_files(directory, expected, report.errors)
    if len(report.errors) >= MAX_REPORTED_ERRORS:
        return

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        dataset_id = entry.get("dataset_id")
        version = entry.get("version")
        config = configs.get(dataset_id)
        if config is None:
            report.errors.append(f"{dataset_id}: dataset is not in the pinned Eurostat configuration")
            continue
        release = directory / "datasets" / str(dataset_id) / str(version)
        expected_path = f"managed-batches/{batch_id}/datasets/{dataset_id}/{version}"
        if entry.get("path") != expected_path:
            report.errors.append(f"{dataset_id}: path does not match batch/dataset/version")
        if not release.is_dir() or any(not (release / name).is_file() for name in REQUIRED_FILES):
            report.errors.append(f"{dataset_id}: release bundle is incomplete")
            continue
        try:
            metadata = read_yaml(release / "dataset.yaml")
            provenance = read_json(release / "provenance.json")
        except Exception as error:
            report.errors.append(f"{dataset_id}: unable to parse release metadata: {error}")
            continue
        validate_schema(
            metadata, CONTRACTS / "dataset.schema.json", f"{dataset_id}/dataset.yaml", report.errors
        )
        validate_schema(
            provenance,
            CONTRACTS / "eurostat-provenance.schema.json",
            f"{dataset_id}/provenance.json",
            report.errors,
        )
        if metadata.get("id") != dataset_id:
            report.errors.append(f"{dataset_id}: dataset.yaml id does not match its path")
        versions = metadata.get("versions", [])
        if len(versions) != 1 or versions[0].get("version") != version:
            report.errors.append(f"{dataset_id}: dataset.yaml must declare exactly path version")
        elif versions[0].get("immutable") is not True:
            report.errors.append(f"{dataset_id}: release must be immutable")
        upstream = metadata.get("upstream", {})
        if metadata.get("source_type") != "official" or upstream.get("provider") != "Eurostat":
            report.errors.append(f"{dataset_id}: managed Eurostat data must be an official source")
        if upstream.get("dataset_code") != config["dataset_code"]:
            report.errors.append(f"{dataset_id}: upstream dataset_code is not pinned")
        if upstream.get("filters") != config["filters"]:
            report.errors.append(f"{dataset_id}: upstream filters are not pinned")
        if provenance.get("dataset_code") != config["dataset_code"]:
            report.errors.append(f"{dataset_id}: provenance dataset_code is not pinned")
        row_count, _ = validate_observations(
            release / "observations.csv", metadata, report.errors
        )
        report.observations += row_count
        if entry.get("observations") != row_count:
            report.errors.append(f"{dataset_id}: manifest observation count does not match CSV")
        if provenance.get("observations_sha256") != sha256_file(release / "observations.csv"):
            report.errors.append(f"{dataset_id}: provenance observations checksum mismatch")
        actual_digests = {
            filename: sha256_file(release / filename) for filename in sorted(REQUIRED_FILES)
        }
        if entry.get("artifact_sha256") != actual_digests:
            report.errors.append(f"{dataset_id}: manifest artifact checksums do not match release")
        report.artifact_sha256[str(dataset_id)] = actual_digests
    report.datasets = len(entries)
    if report.observations > int(limits["maximum_total_observations"]):
        report.errors.append("batch exceeds the configured total observation limit")


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate one immutable managed intake batch")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--batch-dir", type=Path)
    source.add_argument("--base-sha")
    parser.add_argument("--head-sha")
    parser.add_argument("--pr-number", type=int)
    parser.add_argument("--repository", type=Path, default=ROOT)
    parser.add_argument("--report", type=Path, default=Path("managed-validation-report.json"))
    return parser.parse_args()


def main() -> None:
    args = arguments()
    report = ManagedBatchReport(pull_request=args.pr_number, head_sha=args.head_sha)
    try:
        verify_pinned_contracts(report.errors)
        if args.batch_dir:
            directory = args.batch_dir.resolve()
        elif not args.head_sha or args.pr_number is None:
            report.errors.append("--base-sha requires --head-sha and --pr-number")
            directory = None
        else:
            directory = resolve_pr_batch(
                args.base_sha, args.head_sha, report.errors, args.repository.resolve()
            )
        if directory is not None:
            validate_managed_batch(directory, report)
    except Exception as error:
        report.errors.append(f"validator error: {error}")
    report.valid = not report.errors
    output = json.dumps(asdict(report), ensure_ascii=False, indent=2) + "\n"
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(output, encoding="utf-8")
    print(output, end="")
    raise SystemExit(0 if report.valid else 1)


if __name__ == "__main__":
    main()
