---
id: IDEA-0024
title: FDE triage session facilitator agent (ac-fde-triage skill)
status: parked
tags:
- fde
- triage
- agent-team
- workflow
- jira
- confluence
- feedback-loop
- persistent-memory
- prompt-tuning
size: L
created: '2026-06-29'
updated: '2026-06-30'
last_reviewed: '2026-06-29'
promoted_to: null
pattern: null
archive_reason: null
---

# IDEA-0024: FDE triage session facilitator agent (ac-fde-triage skill)

## Description

End-to-end agent support for weekly FDE Architect triage sessions, spanning pre-session prep, live facilitation, post-session reconciliation, and longer-term learning.

### Phase 1 — Pre-session (`ac-fde-triage` skill)

Wraps FDE Triage Bot v2 Confluence prompts in RSP-style gated workflow:

- Query: `project = "Forward Deployed Engineering" AND assignee is EMPTY AND status = "Backlog" AND parent is EMPTY AND type = Story`
- Per-ticket Gap Analysis → Research → Respond (pages 6639091740, 6639943712, 6639419428)
- Completeness scoring against CS Engineering Requirements Gathering Template
- **Glean self-research step** — after gap analysis, agent queries Glean MCP to try to answer its own open questions before escalating:
  - Enterprise search: product roadmap docs, prior art, client context, existing DE reports
  - Code search: similar implementations, known connector patterns, FDE-built app inventory
  - People search: SME identification for routing recommendations
  - If resolved → enrich analysis with cited sources and confidence level
  - If unresolved → auto-comment on Jira ticket asking the submitter the specific questions Glean couldn't answer
- Ranked Architect agenda with recommended disposition per ticket (Accept / Request info / Divert / Close / Defer)
- Human owns all routing decisions — agent advises only

Start Path A: skill only, no new infra. Live pilot validated against 10-ticket queue (2026-06-29).

### Phase 2 — Session hub (Confluence)

Create a **per-session Confluence page** in the FDE space as the single source of truth for each triage call:

- Session metadata (date, participants, JQL snapshot, ticket count)
- Per-ticket section: Jira link, gap-analysis summary, agent recommendation, completeness score, critical gaps
- Space for Architect to capture live decisions and rationale during the call
- Links back to canonical prompt pages and requirements templates
- Becomes the audit trail that Jira comments alone don't provide

**Page template:** `FDE Triage Session — YYYY-MM-DD` under a parent index page (e.g. `FDE Triage Sessions`).

### Phase 3 — Post-session reconciliation (`ac-fde-triage-reconcile` skill)

Follow-up skill that ingests the triage call transcript (Fathom or equivalent) and reconciles against the session hub + agent pre-work:

1. **Decision diff** — what the agent recommended vs what the Architect actually decided per ticket
2. **Rationale extraction** — capture why decisions diverged (agent missed context, human had tribal knowledge, prompt gap, etc.)
3. **Prompt-tuning signals** — classify divergences into actionable categories:
   - Gap Analysis rubric miscalibration (wrong section applicability, missed diversion)
   - Research gaps (prior art not surfaced, wrong Confluence space searched)
   - Classification errors (product vs extensibility, connector vs automation)
   - Template/format issues (intake quality thresholds too strict or too loose)
4. **Output:** structured reconciliation report on the session Confluence page + optional PR to Confluence prompt pages (HITL)

Composes with existing Fathom MCP harness and IDEA-0016 transcript analysis patterns.

### Phase 4 — Persistent memory (longer-term)

Enable agent "learning" across sessions for richer prior-art and routing context:

- **Session memory store** — indexed decisions, divergences, and outcomes per ticket pattern (customer, category, disposition)
- **Prior-art graph** — link tickets → Confluence session pages → final dispositions → similar future intakes
- **Prompt versioning** — track which prompt version produced each pre-session recommendation; correlate with reconciliation accuracy
- **Candidate backends:** FDE KB pipeline (pages 6796836907, 6841499799), hub parking-lot pattern, or lightweight JSON ledger adjacent to skill config

Not in scope for Path A pilot. Requires architecture decision on freshness contract, write path, and whether memory is engagement-scoped or team-scoped.

## Originating context

CoS triage session 2026-06-29. User ideation on FDE triage facilitator agent. Research fan-out across FDE Confluence space, Jira backlog patterns, and `ac-pm-rsp-triage` skill as template. Live pilot prepared for 4pm triage call using canonical FDE Triage JQL (10 unassigned parent-less Backlog Stories).

Extension 2026-06-29: add Confluence session hub, post-session transcript reconciliation skill for prompt-tuning, and longer-term persistent memory for prior-art learning.

## Value hypothesis

**Pre-session (Phase 1):** Scoring 10-ticket triage queue before weekly Architect session saves ~15–20 min/ticket of cold-read time; compounds to 2–3 hrs/month redirected to decisions.

**Session hub (Phase 2):** Single Confluence page per session gives Architects a shared working surface and creates a durable audit trail beyond Jira comments — reduces "what did we decide last week?" friction.

**Reconciliation (Phase 3):** Closing the loop between agent recommendations and human decisions surfaces prompt gaps systematically instead of ad hoc. If 2–3 tuning signals per session × ~4 sessions/month, prompt quality compounds without manual transcript review.

**Persistent memory (Phase 4):** Cross-session prior-art ("we routed a similar BAYADA connector last month as Automation, not Integration") reduces repeated research and improves routing consistency as the decision corpus grows.

## Relationship to other IDEAs

| IDEA | Relationship |
|------|--------------|
| **IDEA-0016** (agent effectiveness tooling) | Phase 3 shares transcript parsing infra; IDEA-0016 is meta/eval, IDEA-0024 is domain-specific FDE triage loop |
| **IDEA-0023** (config decision graph) | Phase 4 memory store may compose with FDE KB pipeline architecture |
| **IDEA-0020** (orchestrator role) | Phase 1 may dispatch subagents per ticket at scale |
| **`ac-pm-rsp-triage`** | Pattern source for gated workflow; not a duplicate |

## Proposed artifact tree

```
ac-fde-triage/
├── SKILL.md
├── fde_triage_config.json          # JQL, prompt page IDs, category routing
└── references/
    ├── disposition-matrix.md        # Accept / Divert / Close / Defer / Won't Do
    ├── glean-research-queries.md    # query templates for self-research step
    └── confluence-session-template.md

ac-fde-triage-reconcile/
├── SKILL.md
└── references/
    ├── reconciliation-rubric.md    # divergence categories → prompt actions
    └── transcript-ingest.md        # Fathom / manual paste workflow
```

## Notes

### Pilot outcomes (2026-06-29)

10-ticket queue from canonical JQL. Recommended session split: 3 assign (FDE-1908, FDE-1895, FDE-1905), 2 close/divert (FDE-1886, FDE-1903), 1 product diversion (FDE-1907), 4 info-request/feasibility (FDE-1906, FDE-1901, FDE-1882, FDE-1909).

FDE-1906 flagged: July 1 go-live unrealistic. FDE-1895: delivery timeline thread (end of July vs July 15).

**Confluence session hub published:**
- Index: https://alayacare.atlassian.net/wiki/spaces/FDE/pages/7055114473/FDE+Triage+Sessions
- Session: https://alayacare.atlassian.net/wiki/spaces/FDE/pages/7054557391/FDE+Triage+Session+2026-06-29

### Reconciliation results (2026-06-30, from Fathom transcript)

**Accuracy: 0/10 exact, 6 partial, 4 miss.** No recommendation was wrong enough to cause harm (human-in-the-loop held), but the agent needs significant context enrichment to become genuinely useful.

#### Decision diff

| Ticket | Agent Rec | Actual Decision | Verdict |
|--------|-----------|-----------------|---------|
| FDE-1886 | Close | Route to TS (data migration) | Partial |
| FDE-1903 | Divert → Product | Assign to John (app bug/enhancement) | Miss |
| FDE-1908 | Accept, assign | Leave in Backlog, ask if DE report exists | Partial |
| FDE-1895 | Accept, timeline | Email Jay re AlayaFlow pricing; 60+ days | Miss |
| FDE-1905 | Accept, assign | Route to TS (OOB custom automation) | Miss |
| FDE-1906 | Request info | Ping Marie — likely paper timesheets, use mobile | Partial |
| FDE-1907 | Divert → Product | Close Won't Do, comment to contact Caleb | Partial |
| FDE-1901 | Request info | Assign to Hugh for discovery | Partial |
| FDE-1882 | Request info | Matthew picks up (returning ticket from prior session) | Miss |
| FDE-1909 | Request info | Hugh generates estimate (webhook→task); UX mismatch flagged | Partial |

#### Prompt-tuning signals (ranked by impact)

1. **AlayaFlow product roadmap** (Critical) — Agent has zero awareness of AlayaFlow agents (RCM billing, visit verification). Caused complete miss on FDE-1895. **Glean-addressable:** enterprise search for AlayaFlow roadmap, T3 planning docs, agent capability pages.
2. **OOB pattern recognition** (High) — FDE-1905 is a known repeatable pattern ("done a lot of times"). **Glean-addressable:** code/ticket search for prior skill-expiration implementations, connector docs.
3. **"Defer to next session" disposition** (High) — FDE-1908 showed that no-action is valid when blocking questions exist. Fix: add Defer disposition triggered by unresolved prerequisite questions remaining after Glean self-research.
4. **Won't Do + redirect comment** (Medium) — FDE-1907 can't be moved cross-project in Jira. Fix: add Won't Do disposition with redirect comment template.
5. **Prior session memory** (Medium, Phase 4) — FDE-1882 was a returning ticket. **Glean-addressable:** search for ticket key in prior session hub pages and Slack threads.
6. **Paper/physical process detection** (Low-Medium) — FDE-1906 was paper timesheets masquerading as digital integration. **Glean-addressable:** search for client context, mobile app availability docs.
7. **FDE-owned app classification** (Low-Medium) — FDE-1903 is a bug on an app the team built. **Glean-addressable:** code search for app ownership, FDE project inventory.

**5 of 7 signals are Glean-addressable** — a self-research step using Glean MCP would dynamically resolve most context gaps without maintaining static reference lists (OOB patterns, roadmap mappings, app inventories). Static lists go stale; Glean stays current.

#### Process improvements (from team feedback)

- **Auto-post open questions pre-session** — Matthew's explicit request: agent should comment on tickets with missing info so submitters can answer before the call. Converts cold-read to pre-answered.
- **TS fast-path filter** — first-pass check for OOB patterns and data migration; skip full gap analysis and route directly (would have handled 2/10 tickets).
- **Capture assignments + work type** — session hub should record: assignee, work type (connector/automation/app bug/OOB/discovery/estimate), next action (comment/email/wait/begin).
- **Prospect vs customer flag** — FDE-1909 was a prospect; triage calculus differs (estimate generation, pre-sales UX gap).
- **Returning ticket highlight** — flag tickets appearing in consecutive sessions with link to prior session's notes (lightweight Phase 4 precursor).

### Open questions for promote

1. Confluence page creation: agent writes directly (needs IT-18465 / MCP write) or drafts for human publish?
2. Transcript source: Fathom auto-ingest vs manual paste into reconcile skill?
3. Memory scope: per-engagement, per-customer, or team-wide FDE corpus?
4. Prompt versioning: Confluence page versions sufficient, or explicit version tags in session hub?
5. AlayaFlow roadmap source: manual maintenance in skill config, or query from product team artifact?
6. Pre-session auto-comment: comment directly on Jira, or draft comments for human review first?
