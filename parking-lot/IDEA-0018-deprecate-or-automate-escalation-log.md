---
id: IDEA-0018
title: Deprecate or automate escalation log
status: promoted
tags:
- template
- observability
- escalation
- overhead-reduction
size: S
created: '2026-06-01'
updated: '2026-06-01'
last_reviewed: '2026-06-01'
promoted_to: ai-hub-poc/FR-0022
pattern: null
archive_reason: null
---
# IDEA-0018: Deprecate or automate escalation log

## Description

### Problem

The escalation log (`template/.agent-team/escalation-log.md`) exists in every bootstrapped project but was never used in any observed session (0/7 sessions, 0/13 subagent invocations). Agents read the file as part of their role preamble but never write to it. The `observability.md` describes escalation patterns, but they are aspirational — no automated mechanism triggers log entries.

This creates two costs:
1. **Read overhead:** Every subagent that reads role/team context encounters the escalation log reference and may read the file, adding a wasted tool call.
2. **Cognitive noise:** The presence of an always-empty file in the template signals a convention that doesn't match reality, reducing trust in other template conventions.

### Proposed solution

Two options (prefer A if feasible):

**Option A — Automate escalation logging (preferred):**
Wire escalation events into the deploy-gate or escalation-matrix as side-effects. When an agent encounters a condition that matches the escalation matrix (e.g., scope change detected, blocked on external dependency, CI failure after 2+ retries), the gate/hook automatically appends a timestamped entry to the escalation log. This makes the log a useful diagnostic artifact without requiring agents to remember to write to it.

Implementation:
- Add escalation-log write logic to `template/scripts/deploy-gate.py` (on gate failure)
- Add optional hook in `template/.agent-team/hooks/` that fires on escalation conditions
- Update `template/.agent-team/escalation-matrix.md` to reference automated logging

**Option B — Deprecate the escalation log:**
Remove `template/.agent-team/escalation-log.md` from the template and strip references from role definitions and observability docs. Acknowledge that escalation tracking happens implicitly through parent-agent chat context and git history, not through a dedicated log file.

Implementation:
- Delete `template/.agent-team/escalation-log.md`
- Update role definitions that reference it
- Update `template/.agent-team/observability.md` to remove log references

### Where it belongs

Template-level improvement:
- `template/.agent-team/escalation-log.md` — either automate or remove
- `template/.agent-team/escalation-matrix.md` — update references
- `template/.agent-team/hooks/` — add logging hook (Option A)
- `template/scripts/deploy-gate.py` — add logging on gate failure (Option A)
- Role definition files that reference the escalation log

## Originating context

Derived from IDEA-0016 ("Agent effectiveness analysis tooling") transcript analysis of 7 parent sessions and 13 subagent transcripts (2026-05-28 through 2026-06-01).

- `derived_from: IDEA-0016`
- Key finding: Template component effectiveness scoring rated escalation log as "NOT USED — aspirational overhead"
- Evidence: 0/7 sessions wrote to or referenced an escalation log; `observability.md` describes escalation patterns but they are not operational

## Value hypothesis

Removes a dead-weight template component that adds read overhead to every subagent session. Under Option A (automate), transforms a zero-value file into a useful diagnostic artifact that captures escalation events automatically. Under Option B (deprecate), eliminates ~1 wasted read per subagent session and reduces cognitive noise in the template.

**ROI estimate:** Option B is ~15 minutes of template cleanup. Option A is ~1-2 hours but produces a genuinely useful escalation audit trail. Either option has immediate payback by removing a known-unused component.

## Notes

- The escalation *matrix* (conditions → actions) IS useful and should be preserved regardless — this is only about the *log* file
- Option A requires touching protected files (`deploy-gate.py`, hooks) — needs explicit human approval per CLAUDE.md rules
- Consider whether the pattern catalog should capture "unused template component" as a recurring signal (see also: role preamble ceremony findings from IDEA-0016)
