---
id: IDEA-0002
title: Finalize must gate on deploy-gate before opening / un-drafting a PR
status: parked
tags:
- dispatcher
- workflow
- quality-gate
- traceability
size: S
created: '2026-05-06'
updated: '2026-05-06'
last_reviewed: '2026-05-06'
promoted_to: null
pattern: null
archive_reason: null
---

# IDEA-0002: Finalize must gate on deploy-gate before opening / un-drafting a PR

## Description

`scripts/dispatch.py` finalize commands push the role branch, then call `gh pr create` (dev/bkf) or `gh pr review` (rev) directly. There is no pre-flight check that the deploy gate (`scripts/deploy-gate.py --stage full`) passes before a PR is opened or un-drafted.

This let two PRs (#10 FR-0008, #11 FR-0006) squash-merge into `main` with zero `@covers` test coverage. The condition was hidden because the deploy gate only checks coverage on FRs in `{in-review, merged}` — and the FR's frontmatter status was still `ready` on `main` at merge time (the dev branch may have flipped it to `in-progress` locally, but that flip never landed). Net result: the gate's coverage check was running on the wrong set of FRs the whole time.

`reconcile-merged` (now landed as IDEA-0001's first piece) closes the post-merge half of the loop. This IDEA closes the pre-merge half: finalize should refuse to open / un-draft a PR (or refuse to mark the rev verdict as `Approve`) when the gate is red.

## Scope when promoted

1. **Pre-flight gate in `_finalize_dev` and `_finalize_bkf`**: run `python scripts/deploy-gate.py --stage full` from the worktree before `gh pr create` (and before flipping `--ready` if `--ready` is requested). On red gate: print failures, refuse, exit non-zero. Exception: `--draft` PRs may pass with a red gate (they're work-in-progress by definition); the gate must be green at `--ready` time.
2. **Gate in `_finalize_rev` for `Approve` verdicts**: a Reviewer that proposes `Approve` while the gate is red is wrong by definition. Refuse to post the verdict; require either gate-green or `Request changes` / `comment`.
3. **Stage-aware gate**: dev finalize uses `--stage dev` (lighter); rev `Approve` and bkf finalize use `--stage full` (the gate's coverage check fires). This matches the gate's existing stage flag.
4. **Override**: `--force` flag for human-driven exceptions, with a stderr warning that surfaces in the PR body. Use sparingly.
5. **Tests**: parameterised tests against a fake gate script that returns 0/1 in different conditions, asserting finalize correctly refuses.

## Why this matters structurally

The dispatcher's current contract is "open the PR; the human merges". The merge gate is GitHub's branch protection (or, in this repo, none). That places the entire load of "is this mergeable?" on the human reviewer. The deploy gate exists precisely so the human doesn't have to track AC coverage by hand. Finalize bypassing the gate breaks the contract — the gate is unenforced anywhere a human's merge button is the next click.

This is a small, well-contained fix (~50 LOC + tests) and it removes the most common foot-gun in the spec-driven workflow.

## Compounding-value hypothesis

Every project that runs the dispatcher inherits this fix once the dispatcher lifts to `template/scripts/` (IDEA-0001). At that point the gate-pre-flight is the cheapest possible enforcement of the "@covers traceability is non-negotiable" rule that already lives in every project's `CLAUDE.md`. Estimated rate of caught-pre-merge gaps: roughly 1 per FR-cycle in the early stages of a project, dropping as agents internalize the contract; over an engagement of 30 FRs, that's 30 round-trips a human doesn't have to drive.

## Originating context

Discovered 2026-05-06 after reconcile-merged flipped FR-0006/FR-0008 to merged: deploy-gate showed missing @covers for AC-1..AC-12 (FR-0006) and AC-1..AC-14 (FR-0008). The PRs (#10, #11) had squash-merged without ever including tests. PR #10's file list: src/, config/, specs/ frontmatter only. The reviewer cycle ran (logs are in _dispatch/) but the rev branch's tests never made it back into the dev branch before the squash-merge. Finalize today is gh-only; it doesn't run deploy-gate.py before gh pr create or gh pr ready.

## Value hypothesis

Block PRs from leaving draft (or being opened ready) when the deploy gate is red, so we never merge an FR without @covers traceability and surface the gap pre-merge instead of post-merge.

## Notes

(none)
