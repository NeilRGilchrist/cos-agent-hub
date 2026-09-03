---
id: IDEA-0029
title: Dispatched agents cannot stage or commit, stranding completed work in worktrees
status: parked
tags:
- dispatch
- control-plane
- harness
- ai-hub-poc
size: M
created: '2026-08-12'
updated: '2026-08-12'
last_reviewed: '2026-08-12'
promoted_to: null
pattern: null
archive_reason: null
---

# IDEA-0029: Dispatched agents cannot stage or commit, stranding completed work in worktrees

## Description

(no description provided)

## Originating context

Defect D2 in _dispatch/wave-2026-08-07T14-36-57-recovered.md, flagged as worth-an-FR but never filed. The FR-0060 Developer was denied the commit tool; the FR-0064 Reviewer left its test file untracked.

## Value hypothesis

Wave 2026-08-07 lost 4 of 4 Developer outputs to this; each occurrence costs a full FR of rework because the FR-0069 staging-as-intent contract reads an unstaged tree as no-work-done. Fixing it once removes the single largest recurring source of manual wave recovery.

## Notes

### Root cause identified 2026-08-12

The FR-0058 maintainer run reproduced this and named the cause: `~/.claude/settings.json`
declares `"ask": ["Bash(git commit *)", ...]`. An `ask` rule opens an interactive prompt,
and a `claude -p` spawn is non-interactive, so there is nobody to answer it. The call is
therefore refused every time. Per-worktree `.claude/settings.local.json` allow-lists written
by the dispatcher do not override a user-level `ask`.

This makes the failure deterministic rather than intermittent, which changes the fix from
"harden retry/recovery" to "stop routing dispatched agents through an interactive gate."
Candidate directions: drop `git commit` from the user-level `ask` list and rely on the
per-worktree deny rules the dispatcher already writes; or have the dispatcher own the commit
the way it already owns push and PR creation.

### Second, related harness block (same run)

The maintainer could not write `.claude/commands/orchestrator.md` or
`.cursor/commands/orchestrator.md` even though both are declared in FR-0058's `owns:` and were
allow-listed. Claude Code applies a built-in guard to `.claude/`-scoped paths, and dispatch
worktrees live at `.claude/worktrees/<role>-<FR>/`, so *every* path inside *every* worktree is
`.claude/`-scoped. Any maintainer FR that owns a `.claude/**` file is structurally unable to
write it from its own worktree. Worth considering whether worktrees should be relocated
outside `.claude/`.
