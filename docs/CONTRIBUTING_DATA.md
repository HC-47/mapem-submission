# Contributing a territorial dataset

## Purpose

This repository gives community datasets a public, auditable path into Open Geo Data Map. GitHub provides
fork identity, immutable commit references, discussion and review. The Open Geo Data Map server remains the
authoritative state machine and PostgreSQL remains the operational data store.

## Lifecycle

```text
fork → submitted → validating → awaiting_vote → approved → merged → promoting → published
                         └───────────────→ changes_requested / rejected
```

The server records the repository, pull request, head SHA, checksums, validation result, voters, approval
reference, merge SHA, promotion pull request and final publication batch. GitHub labels mirror state for
humans but are not the authoritative database.

## Required files

### `dataset.yaml`

Metadata must validate against `contracts/dataset.schema.json`. It defines stable identity, publisher,
source, licence, methodology, geography, periods, indicators, quality and the proposed immutable version.

Community submission does not mean community ownership of the source. A contributor may standardise public
official or research data, but must accurately declare `source_type`, provenance and reuse rights.

#### Making a dataset discoverable by local agents

New submissions can include safe, declarative semantic hints directly in `dataset.yaml`:

```yaml
semantic_hints:
  aliases: [densita di popolazione, population concentration]
  topics: [demography, urbanisation]
  example_questions:
    - Quali province europee hanno la densita piu alta nel 2024?
  caveats:
    - Values are annual regional estimates, not real-time counts.
```

Each indicator may also contain `aliases` and `example_questions`. These strings are versioned with the
submission and help the managed MCP resolver select stable ids. They are never executed as prompts.
`system_prompt`, roles, instructions, templates and arbitrary additional properties are rejected by the
schema. Prompt ownership stays in the trusted core service, which treats all fork metadata as untrusted data.

### `observations.csv`

The exact header is:

```csv
dataset_id,indicator_id,geo_scheme,geo_version,geo_level,geo_code,period,value,unit,statistic,sample_size,quality_flags
```

Each row is one numeric observation. Missing observations are absent rows, never zeroes. The tuple
`(indicator_id, geo_code, period)` must be unique. NUTS observations must reference the pinned NUTS 2024
lookup included under `contracts/`.

### `provenance.json`

Provenance must validate against `contracts/provenance.schema.json` and include source URL, acquisition
timestamp, source checksum, output checksum and declared transformations. The observations checksum must
match the exact submitted CSV bytes.

## Automated contract

The pull-request workflow rejects:

- more than one submission directory;
- modifications outside `submissions/`;
- modification or deletion of an existing submission;
- missing or additional files;
- symlinks, executable files or oversized payloads;
- invalid metadata or provenance schemas;
- identity/version disagreement between path and metadata;
- malformed CSV columns or non-finite values;
- undeclared indicators, periods, units or statistics;
- invalid NUTS 2024 codes or levels;
- duplicate observations or checksum mismatches;
- row counts inconsistent with quality metadata.

Current limits are 25 MiB and 250,000 observations per pull request. Larger datasets will later use immutable
object storage with a checksummed manifest; Git LFS pointers are not accepted as observations.

## Review and voting

Validation only proves structural conformance. Reviewers still assess:

- whether the source is credible and correctly attributed;
- whether the licence permits repository redistribution and intended reuse;
- whether methodology and units are understandable;
- whether geographic and temporal coverage claims are honest;
- whether transformations introduce bias or silent aggregation;
- whether the dataset duplicates or conflicts with an existing canonical dataset.

The initial governance contract is in `contracts/governance.yaml`. Eligible GitHub contributors submit an
`APPROVE` or `REQUEST_CHANGES` review. At least one maintainer remains responsible for final promotion.

Any new commit changes the head SHA, invalidates the validation report and clears every previous vote.

## Approval record

The server writes an immutable decision record containing:

```json
{
  "source_repository": "HC-47/mapem-submission",
  "pull_request": 42,
  "head_sha": "<approved commit>",
  "decision": "approved",
  "eligible_votes": 5,
  "reviewer_approvals": 2,
  "maintainer_approvals": 1,
  "approval_reference": "github:HC-47/mapem-submission#42@<sha>",
  "artifact_sha256": {
    "dataset.yaml": "...",
    "observations.csv": "...",
    "provenance.json": "..."
  }
}
```

## Promotion

Merging an approved submission does not directly modify production. The GitHub App dispatches a promotion
event to the core repository. A trusted core workflow downloads the three files at the approved merge SHA,
revalidates them, creates an immutable release and opens a promotion pull request.

Only the merge of that core pull request can change the global release set. The deployment loader then
inserts every release and switches current versions inside one PostgreSQL transaction.

## Security and abuse handling

- Never include secrets, personal data or credentials in a submission.
- Do not submit data whose redistribution rights are unclear.
- The project may close spam or abusive submissions without opening voting.
- Maintainers can suspend a GitHub account or dataset id from the gateway.
- Validation workflows never use `pull_request_target` to execute a fork checkout.
- Webhook delivery ids, state changes and publication batches are idempotent and auditable.

## Future partner datasets

Private-provider datasets are out of scope for this repository. They may later reuse the same three-file
release bundle through a private intake channel. Public versus restricted visibility will remain separate
from provider ownership and billing.
