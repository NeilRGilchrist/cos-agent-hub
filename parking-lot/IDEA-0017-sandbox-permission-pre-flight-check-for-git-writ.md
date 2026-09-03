---
id: IDEA-0017
title: Sandbox permission pre-flight check for git-write tasks
status: promoted
tags:
- template
- developer-experience
- sandbox
- error-reduction
size: S
created: '2026-06-01'
updated: '2026-06-01'
last_reviewed: '2026-06-01'
promoted_to: ai-hub-poc/FR-0021
pattern: null
archive_reason: null
---
# IDEA-0017: Sandbox permission pre-flight check for git-write tasks

## Description

### Problem

Agents working on git-heavy tasks (commit, push, branch management) inside the Cursor shell sandbox hit permission errors on `git add`, `git commit`, and `git push`. They then spend 15-20 tool calls trying workarounds (different quoting, `--no-verify`, `git commit -a`, Windows-style paths) before discovering that `required_permissions: ["all"]` must be requested. This was the single most expensive failure pattern observed in the IDEA-0016 transcript analysis.

In the FR-0020 lifecycle session, the parent agent made ~10 different attempts to commit before identifying the sandbox as the blocker. Each failed attempt generates a shell call, an error read, and a retry — compounding quickly.

### Proposed solution

Add a pre-flight check or early guidance that instructs agents to request appropriate sandbox permissions at the start of git-write tasks rather than discovering the need through repeated failures. Two implementation options:

1. **Rule-based approach (preferred):** Add a Cursor rule in `.cursor/rules/` (and Claude equivalent) that triggers on git-write task context. The rule would instruct the agent: "When your task involves git commits, pushes, or branch operations, request `required_permissions: ['all']` on your first shell call rather than discovering permission needs through errors."

2. **Role definition approach:** Add a "Shell & permissions" section to `template/.agent-team/roles/developer.md` documenting that git write operations require elevated sandbox permissions and should be requested proactively.

### Where it belongs

Template-level improvement:
- `.cursor/rules/` — new rule file for sandbox permission guidance
- `.claude/rules/` — equivalent Claude rule
- Optionally, `template/.agent-team/roles/developer.md` — developer role definition update

## Originating context

Derived from IDEA-0016 ("Agent effectiveness analysis tooling") transcript analysis of 7 parent sessions and 13 subagent transcripts (2026-05-28 through 2026-06-01).

- `derived_from: IDEA-0016`
- Key evidence: FR-0020 lifecycle session — ~10 failed git commit attempts before sandbox permission discovery
- Frequency: 2 of 7 sessions affected (29%)

## Value hypothesis

Eliminates the single most expensive observed failure pattern: **~15-20 wasted tool calls per affected session**. At an observed frequency of 2/7 sessions (29%), this saves ~5-6 tool calls per session on average across all git-write tasks. For the FR-0020-class sessions (full lifecycle with multiple commits), the savings are higher. Implementation cost is minimal — a single rule file with 3-5 lines of guidance.

**ROI estimate:** ~30 minutes of one-time template work saves ~15-20 tool calls every time an agent hits this pattern. Payback within 1-2 sessions.

## Notes

- The Cursor sandbox blocks git write operations by default as a security measure — the fix is guidance, not disabling the sandbox
- This is orthogonal to the shell/PowerShell incompatibility pattern (also from IDEA-0016) which is about Unix vs Windows command syntax
- Could be combined with a broader "environment pre-flight" check that also covers shell syntax detection
- **Template lift outstanding (2026-06-03):** FR-0021 (merged) delivered the rule at `projects/ai-hub-poc/template/.cursor/rules/sandbox-git-write.mdc` but it has not yet been copied to the hub-level `template/.cursor/rules/`. Since FR-0021 is already merged, this needs a small follow-up FR or direct template sync to propagate to all bootstrapped projects.
