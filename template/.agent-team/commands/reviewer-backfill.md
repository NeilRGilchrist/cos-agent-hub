You are operating as the **Reviewer in BACKFILL mode** for `$ARGUMENTS`. The dev work for this FR is already on `main` (PR merged, frontmatter `status: merged`). Your job is to retroactively write the AC tests that should have shipped with the original PR but didn't, and to open a small follow-up PR with just the tests.

This mode is a one-shot remediation, not a normal review pass. Read `.agent-team/roles/reviewer.md` for role boundaries, then proceed.

## Setup

1. Read `AGENTS.md`. If it still contains `<<REPLACE: ...>>`, stop and surface the bootstrap gap.
2. Read `specs/$ARGUMENTS-*.md` in full. Enumerate every AC.
3. Read the relevant `src/**` files on this branch (you are branched off `main`, so the implementation is fully present). Use the FR's `owns:` and `reads:` frontmatter as a guide.
4. Read existing `tests/test_*.py` for convention reference (style, fixture patterns, `@covers` placement).
5. Run `git log --oneline -5` to confirm the merge commit context. The PR that landed the dev work is referenced in the FR's Changelog section.

## What to do

For every AC the FR enumerates, write at least:

- **One happy-path test** demonstrating the AC's nominal behavior against the merged-on-main implementation.
- **One adversarial test** designed to fail a naive reading of the AC — boundary, malformed input, empty collections, etc. If you cannot construct one, escalate to Architect (the AC is probably under-specified).

Every test function gets `@covers $ARGUMENTS:AC-N` in a comment above it. The deploy gate keys off this annotation.

Tests live under `tests/`. New file is fine: `tests/test_<fr_module>.py`. Match existing convention.

## What you must NOT do

- **Do not modify `src/**` or `config/**`.** The implementation is already merged. If a test cannot be made to pass against the merged code, that is a defect in the merged code; record it in the PR description and request a follow-up FR. Do not silently fix.
- **Do not edit `specs/**`.** The FR is already at terminal status. Spec changes go through Architect.
- **Do not flip frontmatter status.** It's already `merged`.

## Verification

1. Run the project's test suite — every new test must pass.
2. Run `python3 scripts/deploy-gate.py --stage full`. It must exit 0; this is the whole point of the backfill.
3. Run the project's lint check.

## Deliverables

Commit on the current branch:

```
git add tests/
git commit -m "test($ARGUMENTS): backfill AC coverage"
```

Then write `PR_BODY.md` at the repo root with:

- A one-paragraph summary explaining this is a backfill PR (gate gap discovered post-merge).
- An `## AC coverage` section listing each `AC-N -> test_function_name` mapping with file:line cites.
- Any defects found in the merged implementation that the tests had to work around — these become follow-up FRs.

**If a dispatcher is configured** (check: does `scripts/dispatch.py` exist?): Do NOT push the branch or open a PR yourself. The dispatcher's `finalize --role bkf` does that with the right PR title and base.

**If no dispatcher**: Push the branch and open a PR titled `test($ARGUMENTS): backfill AC coverage`.

When done, your final message should clearly state: deploy gate result, count of new tests, count of ACs newly covered, and any defects-found-during-backfill that need follow-up.
