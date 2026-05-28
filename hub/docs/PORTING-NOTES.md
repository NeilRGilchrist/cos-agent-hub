# Porting Notes: Pattern 2 Hybrid Architecture

This document records the architectural decisions made when evolving the agent-team-template from its original multi-agent orchestrator design to the current substrate-independent shape.

## Why the architecture changed

The original template assumed a centralized orchestrator could play the supervisor role. In practice, orchestrator tools lacked the compliance posture needed for regulated workloads: activity wasn't captured in audit logs or compliance APIs. The guidance from tool vendors was that orchestrators should not be used for regulated workloads.

Claude Code on Enterprise flows into compliance APIs via OTel telemetry, making it the correct substrate for engagements under healthcare privacy regulation (PHIPA, HIPAA, etc.) or similar compliance requirements.

## What changed

| Area | Before (Orchestrator-centric) | After (Pattern 2 Hybrid) |
|---|---|---|
| Substrate | Orchestrator as supervisor + agent crew | Claude Code / Cursor with role-based sessions |
| Multi-agent shape | Orchestrator manages parallel agents | Architect = dedicated session; Dev/Reviewer = subagents in impl session |
| Role definitions | `.agent-team/roles/{architect,developer,reviewer}.md` | Same files, orchestrator references stripped, data hygiene added |
| Subagent wiring | N/A | New: `.claude/agents/{architect,developer,reviewer}.md` |
| Gate enforcement | Manually run from sessions | New: `.claude/settings.json` hooks fire automatically |
| Data hygiene | Out-of-band rule | New: compliance profiles with regex hooks + hygiene rules in AGENTS.md |
| Audit trail | Not available | OTel → existing audit infrastructure |

## What stays the same (do not re-port)

These are substrate-independent and continue to work as-is from the original template:

- `specs/_template/FR-XXXX-template.md`
- `scripts/index-specs.py`, `scripts/deploy-gate.py`, `scripts/agent-status.py`
- `.agent-team/escalation-matrix.md`
- `.agent-team/handoff-protocols.md`
- `.agent-team/observability.md`
- `.agent-team/escalation-log.md`
- `.github/workflows/agent-gates.yml`
- FR frontmatter schema

## Files in this port (drop into your project fork)

```
CLAUDE.md                                       (replaces template's CLAUDE.md)
.agent-team/roles/architect.md                  (replaces)
.agent-team/roles/developer.md                  (replaces)
.agent-team/roles/reviewer.md                   (replaces)
.agent-team/hooks/phi_regex_check.py            (new — via phipa profile)
.agent-team/hooks/spec_validate_if_changed.py   (new)
.claude/agents/architect.md                     (new)
.claude/agents/developer.md                     (new)
.claude/agents/reviewer.md                      (new)
.claude/settings.json                           (new)
PORTING-NOTES.md                                (this file)
```

After dropping these in, mark both hook scripts executable (on Unix-like systems):

```bash
chmod +x .agent-team/hooks/*.py
```

## What you must fill in before launching

`CLAUDE.md` has `<<REPLACE: …>>` blocks that the agents will see at session start. Filling them in is the difference between "agents have project context" and "agents flounder asking for context every turn":

1. **Project summary** — one paragraph: scope, systems involved, ticket reference.
2. **Tech stack** — runtime, test command, target language(s).
3. **Glossary** — three to five project-specific terms.

Data hygiene sections (if a compliance profile is active) are universal and need no editing.

## How to launch

**Architect session** (dedicated, persistent across the project):

```bash
cd <project-dir>
claude --agent architect
```

Keep this session open across days/weeks. Spec authoring, FR revisions, ADRs all happen here. Close only when you intentionally want to drop context.

**Implementation session** (separate, may be ephemeral per FR or long-running):

```bash
cd <project-dir>
claude
```

Then in-session, invoke subagents by name:

> Use the developer subagent to implement FR-0007. The FR is drafted; here is the relevant context: …

> Now use the reviewer subagent to verify FR-0007 against the implementation. Developer summary: …

The hooks defined in `.claude/settings.json` fire for both sessions and for any subagent invocation within them — spec graph validation, data hygiene checks, and end-of-session deploy gate reports are universal.

## Pilot plan

Two-week pilot on a single project, as previously scoped:

- Week 1: fork, fill in `CLAUDE.md`, run Architect session for spec authoring on one to three FRs.
- Week 1 / Week 2: implementation session(s) running Dev↔Reviewer cycles against those FRs.
- End of Week 2: review `.agent-team/escalation-log.md` patterns. Where did escalations cluster? What does that tell you about role definitions?

The escalation log is the input to v2 of the role files. Resist the urge to revise mid-pilot — let two weeks of data accumulate first.
