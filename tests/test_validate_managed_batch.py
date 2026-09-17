from __future__ import annotations

import csv
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_managed_batch as validator  # noqa: E402


class ManagedBatchContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.batch_id = "eurostat-managed-test"
        self.dataset_id = "eurostat-population-density"
        self.version = "2099.01.01-test"
        self.batch = Path(self.temporary.name) / "managed-batches" / self.batch_id
        self.release = self.batch / "datasets" / self.dataset_id / self.version
        self.release.mkdir(parents=True)
        configs = json.loads(
            (ROOT / "contracts" / "eurostat-datasets.json").read_text(encoding="utf-8")
        )
        config = next(item for item in configs if item["id"] == self.dataset_id)
        metadata = {
            "schema_version": "1.0",
            "id": self.dataset_id,
            "title": "Test managed Eurostat dataset",
            "description": "A complete synthetic managed release used for contract tests.",
            "category": "Tests",
            "publisher": "Eurostat",
            "source": "Eurostat test source",
            "source_url": "https://ec.europa.eu/eurostat/",
            "source_type": "official",
            "status": "official_source",
            "licence": {
                "id": "EC-REUSE-2011-833",
                "name": "European Commission reuse policy",
                "url": "https://ec.europa.eu/info/legal-notice_en",
                "attribution": "Source: Eurostat",
                "commercial_use": True,
                "attribution_required": True,
                "share_alike": False,
                "redistribution": True,
            },
            "methodology": "Synthetic test of the pinned managed Eurostat normalisation contract.",
            "geography": {"scheme": "NUTS", "version": "2024", "levels": [3]},
            "geographic_coverage": ["IT"],
            "temporal_frequency": "annual",
            "temporal_coverage": ["2024"],
            "indicators": [
                {
                    "id": "population-density",
                    "name": "Population density",
                    "unit": "inhabitants/km²",
                    "statistic": "annual_value",
                }
            ],
            "quality": {
                "score": 95,
                "coverage": 1,
                "coverage_by_level": {"3": 1},
                "countries_covered": 1,
                "countries_in_contract": 1,
                "observations": 1,
                "geographies": 1,
                "geographies_by_level": {"3": 1},
                "methodology_documented": True,
                "source_documented": True,
                "reproducible": True,
                "peer_reviews": 0,
                "community_votes": 0,
                "last_update": "2099-01-01",
            },
            "versions": [
                {
                    "version": self.version,
                    "published_at": "2099-01-01",
                    "notes": "Synthetic managed release.",
                    "immutable": True,
                }
            ],
            "created_at": "2099-01-01",
            "updated_at": "2099-01-01",
            "upstream": {
                "provider": "Eurostat",
                "dataset_code": config["dataset_code"],
                "api_base_url": "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data",
                "filters": config["filters"],
            },
        }
        (self.release / "dataset.yaml").write_text(
            yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        columns = json.loads(
            (ROOT / "contracts" / "observations.columns.json").read_text(encoding="utf-8")
        )["columns"]
        with (self.release / "observations.csv").open(
            "w", encoding="utf-8", newline=""
        ) as stream:
            writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
            writer.writeheader()
            writer.writerow(
                {
                    "dataset_id": self.dataset_id,
                    "indicator_id": "population-density",
                    "geo_scheme": "NUTS",
                    "geo_version": "2024",
                    "geo_level": "3",
                    "geo_code": "ITC11",
                    "period": "2024",
                    "value": "100",
                    "unit": "inhabitants/km²",
                    "statistic": "annual_value",
                    "sample_size": "",
                    "quality_flags": "",
                }
            )
        observations_digest = validator.sha256_file(self.release / "observations.csv")
        provenance = {
            "provider": "Eurostat",
            "dataset_code": config["dataset_code"],
            "query_url": "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/demo_r_d3dens?format=JSON",
            "imported_at": "2099-01-01T00:00:00Z",
            "response_sha256": "0" * 64,
            "observations_sha256": observations_digest,
            "upstream_updated_at": "2099-01-01T00:00:00Z",
            "upstream_label": "Synthetic Eurostat response",
            "geography_levels": {"declared": [3], "available_upstream": [2, 3]},
            "normalisation": {
                "geography": "Only NUTS 2024 units are retained, at the declared levels.",
                "missing_values": "Missing upstream values remain absent.",
                "flags": "Upstream quality flags remain available.",
            },
        }
        (self.release / "provenance.json").write_text(
            json.dumps(provenance, indent=2) + "\n", encoding="utf-8"
        )
        artifacts = {
            name: validator.sha256_file(self.release / name)
            for name in sorted(validator.REQUIRED_FILES)
        }
        manifest = {
            "schema_version": "1.0",
            "batch_id": self.batch_id,
            "channel": "managed_public",
            "provider": "Eurostat",
            "generated_at": "2099-01-01T00:00:00Z",
            "geography": {"scheme": "NUTS", "version": "2024"},
            "source_config_sha256": validator.sha256_file(
                ROOT / "contracts" / "eurostat-datasets.json"
            ),
            "datasets": [
                {
                    "dataset_id": self.dataset_id,
                    "version": self.version,
                    "previous_version": "2098.12.01",
                    "path": f"managed-batches/{self.batch_id}/datasets/{self.dataset_id}/{self.version}",
                    "observations": 1,
                    "artifact_sha256": artifacts,
                    "regression": {
                        "previous_observations": 1,
                        "current_observations": 1,
                        "added": 0,
                        "removed": 0,
                        "changed": 1,
                        "coverage_drop": 0,
                        "removed_observation_fraction": 0,
                        "failures": [],
                    },
                }
            ],
            "unchanged_datasets": [],
        }
        (self.batch / "batch.yaml").write_text(
            yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _validate(self) -> validator.ManagedBatchReport:
        report = validator.ManagedBatchReport()
        validator.verify_pinned_contracts(report.errors)
        validator.validate_managed_batch(self.batch, report)
        report.valid = not report.errors
        return report

    def test_valid_managed_batch_passes(self) -> None:
        report = self._validate()
        self.assertTrue(report.valid, report.errors)
        self.assertEqual(report.datasets, 1)
        self.assertEqual(report.observations, 1)

    def test_manifest_artifact_checksum_mismatch_is_rejected(self) -> None:
        manifest_path = self.batch / "batch.yaml"
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        manifest["datasets"][0]["artifact_sha256"]["observations.csv"] = "f" * 64
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
        report = self._validate()
        self.assertTrue(any("artifact checksums" in item for item in report.errors))

    def _rewrite_entry(self, **changes: object) -> None:
        manifest_path = self.batch / "batch.yaml"
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        entry = manifest["datasets"][0]
        regression = changes.pop("regression", {})
        entry.update(changes)
        entry["regression"].update(regression)
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

    def _first_release(self, **regression: object) -> dict[str, object]:
        return {
            "first_release": True,
            "previous_observations": 0,
            "current_observations": 1,
            "added": 1,
            "removed": 0,
            "changed": 0,
            **regression,
        }

    def test_a_first_release_passes_with_a_null_predecessor(self) -> None:
        """A dataset registered in the core and never published enters here.

        The contract used to require `previous_version` as a string, so the
        managed channel could refresh a dataset but never publish its first
        release: the only way in was around the channel.
        """
        self._rewrite_entry(previous_version=None, regression=self._first_release())
        report = self._validate()
        self.assertTrue(report.valid, report.errors)

    def test_a_null_predecessor_must_declare_a_first_release(self) -> None:
        self._rewrite_entry(
            previous_version=None, regression=self._first_release(first_release=False)
        )
        self.assertFalse(self._validate().valid)

    def test_a_first_release_has_nothing_to_remove(self) -> None:
        self._rewrite_entry(previous_version=None, regression=self._first_release(removed=1))
        self.assertFalse(self._validate().valid)

    def test_a_release_with_a_predecessor_cannot_claim_to_be_first(self) -> None:
        self._rewrite_entry(regression={"first_release": True})
        self.assertFalse(self._validate().valid)


if __name__ == "__main__":
    unittest.main()
