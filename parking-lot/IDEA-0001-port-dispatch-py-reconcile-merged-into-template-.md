---
id: IDEA-0001
title: Port dispatch.py + reconcile-merged into template/scripts/
status: archived
tags:
- tooling
- template
- dispatcher
- workflow
size: M
created: '2026-05-06'
updated: '2026-05-28'
last_reviewed: '2026-05-28'
promoted_to: null
pattern: null
archive_reason: 'Already implemented: dispatch.py + reconcile-merged ported to template/scripts/dispatch.py
  (byte-identical to projects/ai-hub-poc/scripts/dispatch.py) in commit 5a467c8. Template/AGENTS.md
  reflects the role-cycle workflow. Open questions from the IDEA (canonical vs drift,
  post-merge hook, third harness adapter) can be raised as follow-up FRs if/when needed.'
---
# IDEA-0001: Port dispatch.py + reconcile-merged into template/scripts/

## Description

`scripts/dispatch.py` is the tick/worktree/lockfile/finalize automation that runs the Dev↔Reviewer cycle for ai-hub-poc. The 2026-05-06 addition of `reconcile-merged` closes a structural gap: GitHub PR merge state was not projecting back into spec frontmatter, so dispatcher runs were re-firing already-merged FRs and gating their dependents.

This whole capability — worktree spawning, role-aware locks, harness adapters (claude-code + cursor), finalize (push + open PR / post review), reconcile-merged — is project-agnostic. Every bootstrapped project gets `index-specs.py`, `deploy-gate.py`, `agent-status.py` from `template/scripts/` today; `dispatch.py` should be the fourth.

## Scope when promoted

- Move `dispatch.py` to `template/scripts/dispatch.py` with no project-specific assumptions (path resolution already uses `REPO_ROOT = Path(__file__).resolve().parent.parent`).
- Generalize hardcoded names: `claude/` branch prefix, `claude-code` default harness, `.claude/worktrees/` path are all project-agnostic but should be reviewed.
- Sync the back-port to ai-hub-poc once the template version stabilizes (or replace ai-hub-poc's copy via a `bootstrap.sh --upgrade` flow if one exists).
- Document the role-cycle loop (tick -> dev -> finalize -> human-merge -> reconcile -> tick) in `template/AGENTS.md`.

## Open questions for promotion

1. Do we want one canonical dispatcher, or per-project copies that drift? (Canonical = lift to `template/scripts/`. Drift-tolerant = keep project-local but document the pattern in `patterns/`.)
2. Should reconcile run as a git `post-merge` hook by default in `template/`, or stay tick-time-only?
3. The harness adapter pair (`claude-code` + `cursor`) is already pluggable — does the template need a third (e.g., GitHub Copilot Workspace) before we lift?

## Originating context

ai-hub-poc grew dispatch.py incrementally; the post-merge reconciler is the latest piece (commits e330f38 / f109d64 surface the bug it fixes). The script is currently project-local but every spec-driven project needs the same loop.

## Value hypothesis

Lift the dispatcher (worktree spawning, lockfiles, finalize, reconcile-merged) into the template so every bootstrapped project gets the same role-cycle automation, including the post-merge projector that closes the GitHub-merge -> spec-graph staleness gap.

## Notes

(none)
