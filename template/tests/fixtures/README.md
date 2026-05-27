# Test Fixtures

This directory holds synthetic test data used by the test suite.

## Synthetic-only data requirement

**All fixture data must be synthetic — generated, not derived from real records.** This is a non-negotiable PHI hygiene rule. See the root `CLAUDE.md` working agreement for the full policy.

Specifically:

- **No real client/patient names, IDs, addresses, DOBs, health card numbers, or SINs.**
- Use ticket IDs (`FDE-XXXX`), FR IDs (`FR-XXXX:AC-Y`), and synthetic identifiers (`Patient A`, `Provider 1`) instead.
- Real-data testing happens in staging environments, never via fixtures committed to the repo.
- If you encounter real PHI in a fixture file, **stop immediately** and flag to the human supervisor.

## Fixture review

All fixture files should be reviewed for PHI hygiene before commit. The `.claude/settings.json` PHI regex hook provides an automated safety net, but human review remains the primary gate.

## Conventions

- Name fixture files descriptively: `valid_fr_frontmatter.yaml`, `malformed_input.json`, etc.
- Keep fixtures minimal — include only the fields needed by the test.
- Document any non-obvious fixture structure in comments or a companion `.md` file.
