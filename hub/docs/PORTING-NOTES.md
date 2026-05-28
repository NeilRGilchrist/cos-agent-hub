# Porting Notes: Cowork → Claude Code (Pattern 2 Hybrid)

This document records the changes made when porting the original agent-team-template (designed when Cowork was on the table) to a pure Claude Code substrate for use on FDE engagements that touch PHI.

## Why we ported

The original template assumed Cowork could play the supervisor/orchestrator role. Cowork's compliance posture rules it out for AlayaCare FDE work: Cowork activity is not captured in Audit Logs, Compliance API, or Data Exports, across all plan tiers including Enterprise. Anthropic's own guidance is that Cowork should not be used for regulated workloads.

Claude Code on Enterprise *does* flow into the Compliance API via OTel telemetry (which AlayaCare has already wired up). That makes it the correct substrate for any engagement under PHIPA.

## What changed

| Area | Before (Cowork-centric) | After (Pure Claude Code, Pattern 2) |
|---|---|---|
| Substrate | Cowork as supervisor + agent crew | Claude Code only |
| Multi-agent shape | Cowork manages parallel agents | Architect = dedicated session; Dev/Reviewer = subagents in impl session |
| Role definitions | `.agent-team/roles/{architect,developer,reviewer}.md` | Same files, Cowork references stripped, PHI hygiene added |
| Subagent wiring | N/A | New: `.claude/agents/{architect,developer,reviewer}.md` |
| Gate enforcement | Manually run from Cowork sessions | New: `.claude/settings.json` hooks fire automatically |
| PHI defenses | "Don't put PHI in Cowork" (out-of-band rule) | New: PHI regex hook + hygiene rules in CLAUDE.md + role files |
| Audit trail | Not available | OTel → AlayaCare's existing audit infrastructure |

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

If any of those files reference Cowork by name in their bodies, do a search-and-replace pass during the fork. The original draft was Cowork-aware in places.

## Files in this port (drop into your engagement repo fork)

```
CLAUDE.md                                       (replaces template's CLAUDE.md)
.agent-team/roles/architect.md                  (replaces)
.agent-team/roles/developer.md                  (replaces)
.agent-team/roles/reviewer.md                   (replaces)
.agent-team/hooks/phi_regex_check.py            (new)
.agent-team/hooks/spec_validate_if_changed.py   (new)
.claude/agents/architect.md                     (new)
.claude/agents/developer.md                     (new)
.claude/agents/reviewer.md                      (new)
.claude/settings.json                           (new)
PORTING-NOTES.md                                (this file)
```

After dropping these in, mark both hook scripts executable:

```bash
chmod +x .agent-team/hooks/*.py
```

## What you must fill in before launching

`CLAUDE.md` has three `[FILL IN]` blocks that the agents will see at session start. Filling them in is the difference between "agents have engagement context" and "agents flounder asking for context every turn":

1. **Engagement summary** — one paragraph: client (or anonymized handle), integration scope, systems involved, FDE ticket reference.
2. **Tech stack** — runtime (Make.com / Lambda / Iguana / Retool), test command, target language(s).
3. **Glossary** — three to five engagement-specific terms.

The PHI hygiene section is universal and needs no editing.

## How to launch

**Architect session** (dedicated, persistent across the engagement):

```bash
cd <engagement-repo>
claude --agent architect
```

Keep this session open across days/weeks. Spec authoring, FR revisions, ADRs all happen here. Close only when you intentionally want to drop context.

**Implementation session** (separate, may be ephemeral per FR or long-running):

```bash
cd <engagement-repo>
claude
```

Then in-session, invoke subagents by name:

> Use the developer subagent to implement FR-0007. The FR is drafted; here is the relevant context: …

> Now use the reviewer subagent to verify FR-0007 against the implementation. Developer summary: …

The hooks defined in `.claude/settings.json` fire for both sessions and for any subagent invocation within them — the spec graph validation, PHI regex check, and end-of-session deploy gate report are universal.

## Pilot plan

Two-week pilot on a single FDE engagement, as previously scoped:

- Week 1: fork, fill in `CLAUDE.md`, run Architect session for spec authoring on one to three FRs.
- Week 1 / Week 2: implementation session(s) running Dev↔Reviewer cycles against those FRs.
- End of Week 2: review `.agent-team/escalation-log.md` patterns. Where did escalations cluster? What does that tell you about role definitions?

The escalation log is the input to v2 of the role files. Resist the urge to revise mid-pilot — let two weeks of data accumulate first.

## What to verify with AlayaCare InfoSec before the pilot starts

- Confirm Claude Code OTel events for this engagement repo flow into the same audit pipeline as other Claude Code usage.
- Confirm the hook scripts (which run as your local user) are acceptable under endpoint policy.
- Confirm the `.claude/settings.json` is committed (project-level, team-shared) rather than local-only — this is what makes the hooks portable to other team members joining the engagement later.

If anything in the above is unclear when you check, that's a flag to pause before the pilot — not after.
