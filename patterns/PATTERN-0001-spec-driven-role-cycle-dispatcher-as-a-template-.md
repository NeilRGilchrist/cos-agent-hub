---
id: PATTERN-0001
name: Spec-driven role-cycle dispatcher as a template capability
status: rejected
tags:
- dispatcher
- workflow
- template
- scaffold
created: '2026-05-28'
updated: '2026-05-28'
instances: []
ideas:
- IDEA-0001
- IDEA-0002
- IDEA-0003
rejection_reason: Valid shape; already implemented as template/scripts/dispatch.py
  (byte-identical to projects/ai-hub-poc/scripts/dispatch.py, lifted in commit 5a467c8).
  What remains parked (IDEA-0002 pre-flight gate, IDEA-0003 fetch-before-reconcile)
  are refinements to that single artifact — not multi-project instances of a reusable
  shape. Promoted as FRs against ai-hub-poc instead.
---
# PATTERN-0001: Spec-driven role-cycle dispatcher as a template capability

## Description

Lift dispatch.py (worktree spawn / lockfiles / finalize / reconcile-merged) into template/scripts/ so every bootstrapped project inherits the same Dev<->Reviewer role-cycle automation.

## Compounding-value hypothesis

Every spec-driven project on this hub needs the identical Dev<->Reviewer cycle. Lifting once vs. re-deriving per-project saves ~2 engineering-weeks of build per new project plus the foot-guns discovered in ai-hub-poc (gate-bypass merges; stale-main reconcile rebases).

## Constituent signals

- IDEA-0001 — (fill in: how this fits)
- IDEA-0002 — (fill in: how this fits)
- IDEA-0003 — (fill in: how this fits)

## Proposed shape

(fill in)

## Alternatives considered

- **Don't build it.** Cost: (fill in)

## Notes

(none)
