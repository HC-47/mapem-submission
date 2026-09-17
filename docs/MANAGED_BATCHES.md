# Managed Eurostat batches

This repository has two independent intake lanes. Community contributors add one proposed dataset under
`submissions/` and use review/voting. The Open Geo Data Map intake bot adds one atomic Eurostat batch under
`managed-batches/` and uses protected automated validation. Both lanes produce the same three-file open
release format; neither can publish directly to the core or PostgreSQL.

## Repository and fork model

Managed intake uses one persistent technical fork owned by the project bot. A scheduled run does not create
a new fork. It synchronizes the fork's default branch, creates `managed/eurostat/<batch-id>`, writes one
batch and opens a PR against `HC-47/mapem-submission`.

The technical fork must be configured in the gateway. A normal GitHub user cannot obtain managed status by
copying the folder layout or setting `channel: managed_public`: the worker registration is signed and the
server verifies the exact configured fork, PR and commit through the GitHub App.

## Required layout

```text
managed-batches/<batch-id>/
├── batch.yaml
└── datasets/
    ├── <changed-dataset-a>/<version>/
    │   ├── dataset.yaml
    │   ├── observations.csv
    │   └── provenance.json
    └── <changed-dataset-b>/<version>/
        ├── dataset.yaml
        ├── observations.csv
        └── provenance.json
```

One PR adds exactly one new batch directory. It may contain several changed datasets, because the release
set must move atomically. Existing paths cannot be modified, renamed or deleted. Extra files, scripts,
archives, symlinks and executable content are rejected.

## `batch.yaml`

The manifest validates against `contracts/managed-batch.schema.json` and declares:

- stable `batch_id`, `provider: Eurostat` and `channel: managed_public`;
- generation timestamp and geography contract `NUTS/2024`;
- SHA-256 of `contracts/eurostat-datasets.json`;
- every changed dataset, candidate and previous version, exact directory and row count;
- SHA-256 of `dataset.yaml`, `observations.csv` and `provenance.json`;
- regression counts, coverage loss, removal fraction and an empty failure list;
- configured datasets checked in the same run whose observations were unchanged.

A dataset's **first release** declares `previous_version: null` and `regression.first_release: true`. The
schema ties the two together: a null predecessor must be a first release, and a first release has no
previous observations, nothing removed or changed, and no coverage loss. Only datasets already registered in
the core — a provider contract entry and a publication profile — can be proposed this way; the core refuses
the claim for any dataset that is published or already has releases on disk.

The `source_config_sha256` prevents a fork from silently changing Eurostat codes or filters. Configuration
changes must first be reviewed in the core and then copied/pinned here by maintainers.

## Release files

### `dataset.yaml`

The normal open dataset schema applies. Managed Eurostat releases must additionally be official sources,
declare provider `Eurostat`, use NUTS 2024 and match the pinned upstream `dataset_code` and filters for their
dataset id. The path id/version and the single immutable version declared in metadata must agree.

### `observations.csv`

The exact header is:

```csv
dataset_id,indicator_id,geo_scheme,geo_version,geo_level,geo_code,period,value,unit,statistic,sample_size,quality_flags
```

The validator checks NUTS codes/levels, periods, indicators, units, statistics, finite values, uniqueness,
quality counts and configured size limits. Missing Eurostat values remain absent rows and status flags are
preserved in `quality_flags`.

### `provenance.json`

Managed provenance validates against `contracts/eurostat-provenance.schema.json`. It preserves the exact
query URL, import time, raw response SHA-256, observations SHA-256, upstream update/label and explicit
normalization rules for geography, missing values and flags.

## Trusted contract copies

`contracts/core-contract.lock.json` pins checksums and upstream locations for:

- the dataset JSON Schema;
- the NUTS 2024 unit lookup;
- the managed batch JSON Schema;
- the Eurostat provenance JSON Schema;
- the six Eurostat importer configurations.

Pull-request validation checks these trusted base-repository copies before reading candidate data. The fork
checkout supplies only untrusted bytes. Candidate code is never run.

## Validation workflow

`.github/workflows/validate-managed-batch.yml` runs for `managed-batches/**`. The check name is
`managed-batch-contract`. It uses:

1. the validator and contracts checked out at the PR base SHA;
2. candidate files checked out separately at the PR head SHA;
3. read-only repository permission and no secrets;
4. `scripts/validate_managed_batch.py` to enforce PR scope and all nested contracts;
5. a JSON artifact recording PR, head, batch, row totals, errors and artifact checksums.

Limits currently allow at most 20 datasets, 250,000 observations per release, 2,000,000 observations in
total and 100 MiB for a complete managed batch. The six current Eurostat imports are below those limits.

Run a local batch validation with:

```sh
python scripts/validate_managed_batch.py \
  --batch-dir managed-batches/<batch-id> \
  --report managed-validation-report.json
```

Validate PR scope with:

```sh
python scripts/validate_managed_batch.py \
  --base-sha origin/main \
  --head-sha HEAD \
  --pr-number 42 \
  --report managed-validation-report.json
```

## Approval, merge and promotion

The intake worker registers the exact PR head and manifest checksum with `POST /v1/managed/intakes` using a
five-minute HMAC request signature. The gateway independently checks the configured technical fork and
GitHub PR. A successful protected `managed-batch-contract` check moves the database record to `approved`.
There is no community voting for this channel.

If a new commit is pushed, approval is cleared and the manifest checksum is removed until a new signed
registration is completed. Merging an unapproved or mismatched head records a rejection and dispatches
nothing.

An approved merge sends a signed promotion event to the core. The core downloads the batch at the merge
SHA, rechecks every checksum and contract, and recomputes regression gates against the current releases.
It then opens a second PR. PostgreSQL changes only after that protected core PR is merged.

## Reviewer checklist

- [ ] PR adds one batch and no file outside its directory.
- [ ] `managed-batch-contract` passed for the current head.
- [ ] The author/head repository is the configured technical fork.
- [ ] Dataset ids and source configuration checksum are pinned.
- [ ] Regression counts and time coverage are plausible.
- [ ] No unexpected large removal, new gap or altered unit appears.
- [ ] Query URLs, response checksums and import times exist for every release.
- [ ] The gateway reports the same head SHA and manifest checksum.

## Failure rules

- Failed/partial download: no batch PR.
- Unchanged observations: dataset is recorded as unchanged and omitted from release files.
- Any invalid dataset: reject the whole batch.
- New commit: validation and signed registration must repeat.
- Stale previous version during core promotion: rerun intake from the new baseline.
- Failed core promotion or loader: no published or current-version state changes.

The future partner lane is not implemented here. It may reuse release files, but must not reuse managed
Eurostat identity, credentials or automatic approval.
