from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import stat
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"
REQUIRED_FILES = {"dataset.yaml", "observations.csv", "provenance.json"}
MAX_REPORTED_ERRORS = 200
SUBMISSION_PATH = re.compile(
    r"^submissions/(?P<dataset>[a-z0-9]+(?:-[a-z0-9]+)*)/(?P<version>[A-Za-z0-9][A-Za-z0-9._-]*)/(?P<file>[^/]+)$"
)
PERIOD_PATTERNS = {
    "annual": re.compile(r"^\d{4}$"),
    "quarterly": re.compile(r"^\d{4}-Q[1-4]$"),
    "monthly": re.compile(r"^\d{4}-(0[1-9]|1[0-2])$"),
    "daily": re.compile(r"^\d{4}-(0[1-9]|1[0-2])-([0-2]\d|3[01])$"),
}


@dataclass
class ValidationReport:
    valid: bool = False
    pull_request: int | None = None
    head_sha: str | None = None
    submission_path: str | None = None
    dataset_id: str | None = None
    version: str | None = None
    observations: int = 0
    geographies: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    artifact_sha256: dict[str, str] = field(default_factory=dict)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("must contain a YAML object")
    return value


def contract_limits() -> dict[str, int]:
    governance = read_yaml(CONTRACTS / "governance.yaml")
    return governance["limits"]


def verify_pinned_contracts(errors: list[str]) -> None:
    lock = read_json(CONTRACTS / "core-contract.lock.json")
    for key in (
        "dataset_contract",
        "geography_contract",
        "managed_batch_contract",
        "eurostat_provenance_contract",
        "eurostat_config_contract",
    ):
        contract = lock[key]
        path = ROOT / contract["path"]
        if not path.is_file():
            errors.append(f"pinned contract is missing: {contract['path']}")
        elif sha256_file(path) != contract["sha256"]:
            errors.append(f"pinned contract checksum mismatch: {contract['path']}")


def git(repository: Path, *args: str) -> str:
    process = subprocess.run(
        ["git", *args],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )
    if process.returncode:
        raise ValueError(process.stderr.strip() or f"git {' '.join(args)} failed")
    return process.stdout


def resolve_pr_submission(
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
    names: set[str] = set()
    for parts in changed:
        status_code, *paths = parts
        path = paths[-1] if paths else ""
        match = SUBMISSION_PATH.fullmatch(path)
        if status_code != "A":
            errors.append(f"{path}: submissions are immutable; only added files are allowed")
            continue
        if not match:
            errors.append(f"{path}: a dataset PR may only add files under one submission directory")
            continue
        roots.add(f"submissions/{match['dataset']}/{match['version']}")
        names.add(match["file"])
    if len(roots) != 1:
        errors.append("a pull request must add exactly one submission directory")
        return None
    root = next(iter(roots))
    if names != REQUIRED_FILES:
        missing = sorted(REQUIRED_FILES - names)
        extra = sorted(names - REQUIRED_FILES)
        if missing:
            errors.append(f"{root}: missing files: {', '.join(missing)}")
        if extra:
            errors.append(f"{root}: additional files are not allowed: {', '.join(extra)}")
    candidate = (repository / root).resolve()
    submissions_root = (repository / "submissions").resolve()
    try:
        relative = candidate.relative_to(submissions_root)
    except ValueError:
        errors.append("submission path escapes the submissions directory")
        return None
    if len(relative.parts) != 2:
        errors.append("submission path must contain exactly dataset id and version")
        return None
    return candidate


def check_bundle_files(directory: Path, errors: list[str]) -> None:
    limits = contract_limits()
    if not directory.is_dir():
        errors.append(f"{directory}: submission directory does not exist")
        return
    actual = {path.name for path in directory.iterdir()}
    missing = sorted(REQUIRED_FILES - actual)
    extra = sorted(actual - REQUIRED_FILES)
    if missing:
        errors.append(f"missing files: {', '.join(missing)}")
    if extra:
        errors.append(f"additional files are not allowed: {', '.join(extra)}")
    limits_by_file = {
        "dataset.yaml": limits["maximum_metadata_bytes"],
        "observations.csv": limits["maximum_observations_bytes"],
        "provenance.json": limits["maximum_provenance_bytes"],
    }
    for filename in sorted(REQUIRED_FILES & actual):
        path = directory / filename
        if path.is_symlink():
            errors.append(f"{filename}: symlinks are not allowed")
            continue
        mode = path.stat().st_mode
        if not stat.S_ISREG(mode):
            errors.append(f"{filename}: must be a regular file")
        if mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
            errors.append(f"{filename}: executable files are not allowed")
        if path.stat().st_size > limits_by_file[filename]:
            errors.append(f"{filename}: exceeds the configured size limit")
        with path.open("rb") as stream:
            prefix = stream.read(8192)
        if b"\x00" in prefix:
            errors.append(f"{filename}: binary content is not allowed")


def validate_schema(
    value: Any,
    schema_path: Path,
    label: str,
    errors: list[str],
) -> None:
    schema = read_json(schema_path)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    for error in sorted(validator.iter_errors(value), key=lambda item: list(item.path)):
        location = ".".join(str(part) for part in error.path) or "$"
        errors.append(f"{label}: {location}: {error.message}")


def load_nuts_units() -> dict[str, dict[str, str]]:
    with (CONTRACTS / "nuts-2024.csv").open(encoding="utf-8", newline="") as stream:
        return {row["geo_code"]: row for row in csv.DictReader(stream)}


def validate_observations(
    path: Path,
    metadata: dict[str, Any],
    errors: list[str],
) -> tuple[int, int]:
    columns = read_json(CONTRACTS / "observations.columns.json")["columns"]
    indicators = {item["id"]: item for item in metadata.get("indicators", [])}
    geography = metadata.get("geography", {})
    declared_periods = set(metadata.get("temporal_coverage", []))
    period_pattern = PERIOD_PATTERNS.get(metadata.get("temporal_frequency"))
    nuts = load_nuts_units()
    seen: set[tuple[str, str, str]] = set()
    geographies: set[str] = set()
    row_count = 0
    maximum = contract_limits()["maximum_observations"]
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != columns:
            errors.append(f"observations.csv: columns must exactly match {columns}")
            return 0, 0
        for line, row in enumerate(reader, start=2):
            if len(errors) >= MAX_REPORTED_ERRORS:
                errors.append(
                    f"observations.csv: stopped after {MAX_REPORTED_ERRORS} validation errors"
                )
                break
            row_count += 1
            if row_count > maximum:
                errors.append(f"observations.csv: exceeds {maximum} observations")
                break
            prefix = f"observations.csv row {line}"
            indicator = indicators.get(row["indicator_id"])
            if row["dataset_id"] != metadata.get("id"):
                errors.append(f"{prefix}: dataset_id does not match dataset.yaml")
            if indicator is None:
                errors.append(f"{prefix}: undeclared indicator {row['indicator_id']}")
            if geography.get("scheme") != "NUTS" or geography.get("version") != "2024":
                errors.append("dataset.yaml: community geography must use the pinned NUTS 2024 contract")
            # `levels` is a list: a dataset carries every level its source
            # publishes it at, so a row belongs to the release when its level is
            # one of them — not when it equals a single declared level.
            declared_levels = {str(level) for level in geography.get("levels", [])}
            if (
                row["geo_scheme"] != geography.get("scheme")
                or row["geo_version"] != str(geography.get("version"))
                or row["geo_level"] not in declared_levels
            ):
                errors.append(f"{prefix}: geography does not match dataset.yaml")
            unit = nuts.get(row["geo_code"])
            if unit is None or unit["geo_level"] != row["geo_level"]:
                errors.append(f"{prefix}: invalid NUTS 2024 code/level {row['geo_code']}")
            else:
                geographies.add(row["geo_code"])
            if row["period"] not in declared_periods or (
                period_pattern is not None and not period_pattern.fullmatch(row["period"])
            ):
                errors.append(f"{prefix}: invalid or undeclared period {row['period']}")
            try:
                value = float(row["value"])
                if not math.isfinite(value):
                    raise ValueError
            except ValueError:
                errors.append(f"{prefix}: value must be a finite number")
            if indicator is not None and (
                row["unit"] != indicator["unit"] or row["statistic"] != indicator["statistic"]
            ):
                errors.append(f"{prefix}: unit/statistic does not match indicator metadata")
            if row["sample_size"]:
                try:
                    if int(row["sample_size"]) < 0:
                        raise ValueError
                except ValueError:
                    errors.append(f"{prefix}: sample_size must be an empty or non-negative integer")
            key = (row["indicator_id"], row["geo_code"], row["period"])
            if key in seen:
                errors.append(f"{prefix}: duplicate observation {key}")
            seen.add(key)
    if row_count == 0:
        errors.append("observations.csv: must contain at least one observation")
    quality = metadata.get("quality", {})
    if quality.get("observations") != row_count:
        errors.append("dataset.yaml: quality.observations does not match observations.csv")
    if quality.get("geographies") != len(geographies):
        errors.append("dataset.yaml: quality.geographies does not match observations.csv")
    return row_count, len(geographies)


def validate_directory(
    directory: Path,
    report: ValidationReport,
    submissions_root: Path | None = None,
) -> None:
    check_bundle_files(directory, report.errors)
    if report.errors:
        return
    try:
        metadata = read_yaml(directory / "dataset.yaml")
        provenance = read_json(directory / "provenance.json")
    except (UnicodeDecodeError, json.JSONDecodeError, yaml.YAMLError, ValueError) as error:
        report.errors.append(f"unable to parse submission metadata: {error}")
        return
    validate_schema(metadata, CONTRACTS / "dataset.schema.json", "dataset.yaml", report.errors)
    validate_schema(
        provenance,
        CONTRACTS / "provenance.schema.json",
        "provenance.json",
        report.errors,
    )
    try:
        root = (submissions_root or ROOT / "submissions").resolve()
        relative = directory.resolve().relative_to(root)
        path_dataset, path_version = relative.parts
        report.submission_path = f"submissions/{path_dataset}/{path_version}"
        if metadata.get("id") != path_dataset:
            report.errors.append("dataset.yaml: id must match the submission path")
        versions = metadata.get("versions", [])
        if len(versions) != 1 or versions[0].get("version") != path_version:
            report.errors.append("dataset.yaml: must declare exactly the version in the submission path")
        elif versions[0].get("immutable") is not True:
            report.errors.append("dataset.yaml: proposed version must be immutable")
        report.dataset_id = path_dataset
        report.version = path_version
    except (ValueError, TypeError):
        report.errors.append("submission directory must be submissions/<dataset-id>/<version>")
    report.observations, report.geographies = validate_observations(
        directory / "observations.csv", metadata, report.errors
    )
    observations_digest = sha256_file(directory / "observations.csv")
    if provenance.get("observations_sha256") != observations_digest:
        report.errors.append("provenance.json: observations_sha256 does not match observations.csv")
    upstream_provider = str(metadata.get("upstream", {}).get("provider", "")).casefold()
    if str(provenance.get("provider", "")).casefold() != upstream_provider:
        report.errors.append("provenance.json: provider must match dataset.yaml upstream.provider")
    report.artifact_sha256 = {
        filename: sha256_file(directory / filename) for filename in sorted(REQUIRED_FILES)
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate one immutable Mapem community submission")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--submission-dir", type=Path)
    source.add_argument("--base-sha")
    parser.add_argument("--head-sha")
    parser.add_argument("--pr-number", type=int)
    parser.add_argument(
        "--repository",
        type=Path,
        default=ROOT,
        help="Git working tree containing untrusted candidate data; contracts still come from this script's repository",
    )
    parser.add_argument("--report", type=Path, default=Path("validation-report.json"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = ValidationReport(pull_request=args.pr_number, head_sha=args.head_sha)
    try:
        verify_pinned_contracts(report.errors)
        if args.submission_dir:
            directory = args.submission_dir.resolve()
        else:
            if not args.head_sha or args.pr_number is None:
                report.errors.append("--base-sha requires --head-sha and --pr-number")
                directory = None
            else:
                repository = args.repository.resolve()
                directory = resolve_pr_submission(
                    args.base_sha, args.head_sha, report.errors, repository
                )
        if directory is not None:
            submissions_root = (
                args.repository.resolve() / "submissions" if not args.submission_dir else None
            )
            validate_directory(directory, report, submissions_root)
    except Exception as error:  # A validator failure must still produce a review artifact.
        report.errors.append(f"validator error: {error}")
    report.valid = not report.errors
    output = json.dumps(asdict(report), ensure_ascii=False, indent=2) + "\n"
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(output, encoding="utf-8")
    print(output, end="")
    raise SystemExit(0 if report.valid else 1)


if __name__ == "__main__":
    main()
