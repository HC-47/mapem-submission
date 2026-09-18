# Mapem data submissions

This public repository is the review boundary for community datasets and bot-managed public Eurostat
refreshes. Community contributors work in their own forks and propose one release; the managed worker uses
one persistent technical fork and proposes one atomic multi-dataset batch. Both paths enforce pinned data
contracts before promotion.

Published datasets do not live here. An approved and merged submission is promoted by a trusted workflow to
[`HC-47/mapem`](https://github.com/HC-47/mapem), where it becomes an immutable
release and is loaded transactionally into PostgreSQL.

## Submission layout

One pull request must add exactly one directory:

```text
submissions/<dataset-id>/<version>/
├── dataset.yaml
├── observations.csv
└── provenance.json
```

The path, `dataset.yaml` identity and declared version must agree. Existing submissions and published
versions are immutable.

## How to submit

1. Fork this repository on GitHub.
2. Create a branch named `dataset/<dataset-id>-<version>`.
3. Add the three required files under `submissions/<dataset-id>/<version>/`.
4. Run `python scripts/validate_submission.py --submission-dir <directory>` locally.
5. Push the branch.
6. Call the Open Geo Data Map community API with the fork, branch and submission path. The server verifies
   ownership and fork ancestry, pins the commit SHA and opens the pull request.
7. Wait for the `submission-contract` check. A trusted workflow adds `contract:valid` or `contract:invalid`.
8. Once valid, request voting through the API. New commits reset validation and every previous vote.

Full contributor and governance documentation is in
[`docs/CONTRIBUTING_DATA.md`](docs/CONTRIBUTING_DATA.md).

## Managed Eurostat intake

Managed refresh PRs add `managed-batches/<batch-id>/batch.yaml` plus the standard three-file release for
every changed dataset — including the first release of a dataset newly registered in the core, which can
enter no other way. They are accepted only from the configured technical fork, use the protected
`managed-batch-contract` check and do not enter community voting. Full contract, security and operational
documentation is in [`docs/MANAGED_BATCHES.md`](docs/MANAGED_BATCHES.md).

## Safety model

- Pull-request validation receives a read-only token and no secrets.
- The validator and contracts are checked out from the trusted base commit, never executed from the fork.
- Contributor code is never executed.
- Only YAML, JSON and CSV data files are accepted.
- Symlinks, binaries, extra files, multiple submissions and oversized payloads are rejected.
- Every decision is pinned to the pull-request head commit and artifact checksums.
- Promotion re-downloads only the three allowed files and validates them again in the core repository.

## Local validation

```sh
python -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/python scripts/validate_submission.py \
  --submission-dir submissions/<dataset-id>/<version>
```

The standalone validator image runs the complete trusted contract suite:

```sh
docker compose build validator
docker compose run --rm validator
```

This image contains contracts and validators only. It has no application credentials, database connection
or provider intake code.

For a managed batch:

```sh
.venv/bin/python scripts/validate_managed_batch.py \
  --batch-dir managed-batches/<batch-id>
```

For pull-request scope validation:

```sh
.venv/bin/python scripts/validate_submission.py \
  --base-sha origin/main \
  --head-sha HEAD \
  --pr-number 42 \
  --report validation-report.json
```

## Repository ownership

Contracts and workflows are owned by the project maintainers. Changes to them are never accepted as part of
a dataset submission. Their evolution happens in separate maintainer-authored pull requests.
