# Tests

This directory holds all tests for the project. Tests are the Reviewer's primary output — they prove that the Developer's code satisfies the acceptance criteria in each FR.

## `@covers` convention

Every test function must carry a `@covers FR-XXXX:AC-Y` annotation in a comment immediately above it. The deploy gate (`scripts/deploy-gate.py`) scans for these annotations and enforces coverage:

- For FRs in `in-review` or `merged` status, **every AC must have at least one `@covers` test**.
- Orphan `@covers` annotations referencing deleted FRs or nonexistent ACs are flagged as failures.

Example (Python):

```python
# @covers FR-0001:AC-1
def test_indexer_parses_example_fr():
    ...

# @covers FR-0001:AC-2
def test_deploy_gate_requires_coverage():
    ...
```

The annotation format is strict: `@covers FR-XXXX:AC-Y` where XXXX is the four-digit FR number and Y is the AC number. Other formats (e.g., `@covers FR-1:AC-1`) will not be detected.

## Directory layout

```
tests/
├── README.md           ← you are here
├── fixtures/           ← synthetic test data only (see fixtures/README.md)
└── unit/
    └── scripts/        ← unit tests for team infrastructure scripts
```

## Role boundaries

- **Reviewer** writes all tests. See `.agent-team/roles/reviewer.md` for the full role definition.
- **Developer** must not write tests for their own code — separation of concerns is the point.
- **Reviewer-backfill** mode (`/reviewer-backfill FR-XXXX`) handles retroactive test writing for FRs already merged without coverage.
