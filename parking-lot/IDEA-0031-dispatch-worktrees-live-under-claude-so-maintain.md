---
id: IDEA-0031
title: Dispatch worktrees live under .claude/, so maintainer agents cannot write .claude-scoped
  files they own
status: parked
tags:
- dispatch
- control-plane
- harness
- worktrees
- ai-hub-poc
size: M
created: '2026-08-12'
updated: '2026-08-12'
last_reviewed: '2026-08-12'
promoted_to: null
pattern: null
archive_reason: null
---

# IDEA-0031: Dispatch worktrees live under .claude/, so maintainer agents cannot write .claude-scoped files they own

## Description

(no description provided)

## Originating context

Surfaced by the FR-0058 maintainer run on 2026-08-12. Dispatch worktrees live at .claude/worktrees/role-FR-id/, so every path inside every worktree is .claude/-scoped and trips the harness built-in guard on that prefix, regardless of the allow-list the dispatcher writes to settings.local.json. The two blocked files were declared in the FR owns: list and explicitly allow-listed.

## Value hypothesis

Any maintainer FR that owns a control-plane file under .claude/ can never satisfy its own acceptance criteria unmodified, so every such FR silently converts into a partial run plus manual human completion. FR-0058 hit this on 2 of its 4 owned files. Relocating worktrees outside .claude/ makes the whole class of control-plane FRs self-completing.

## Notes

(none)
