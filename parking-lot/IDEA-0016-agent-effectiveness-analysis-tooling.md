---
id: IDEA-0016
title: Agent effectiveness analysis tooling
status: parked
tags:
- template
- observability
- meta
- developer-experience
- feedback-loop
size: M
created: '2026-06-01'
updated: '2026-06-01'
last_reviewed: '2026-06-01'
promoted_to: null
pattern: null
archive_reason: null
---

# IDEA-0016: Agent effectiveness analysis tooling

## Description

A Python-based transcript analysis tool (~300-500 lines) that parses `agent-transcripts/*.jsonl` files and produces structured reports on agent session effectiveness. The tool would:

1. **Classify sessions by outcome** — success (task completed, all ACs met), partial (some work done, stalled/errors), failure (blocked, wrong assumptions, never produced output).
2. **Measure context retrieval ROI** — correlate "files read" with "files subsequently modified or referenced in output" to compute a read-to-use ratio per session and per file type.
3. **Detect error pattern clusters** — identify recurring failure modes (shell incompatibility, sandbox permission issues, git repo boundary confusion, CI/local mismatch) with frequency counts.
4. **Score template component usage** — track which role definitions, hooks, and rules actually constrain agent behavior vs. get read but never influence output.
5. **Produce a session efficiency scorecard** — tool calls per FR, elapsed time, retry loops, read-then-discard sequences.

Run monthly or on-demand to surface trends and feed back into template improvements.

## Originating context

Analysis of 7 parent sessions and 13 subagent transcripts (2026-05-28 through 2026-06-01) covering: CoS triage, FR-0020 full lifecycle (architect → developer → reviewer → CI fix), template fixes, Fathom MCP harness dispatch, GitHub-readiness publication, and IDEA-0015 parking.

### Key findings from the analysis

**1. Common error patterns in subagent work**

| Pattern | Frequency | Impact | Evidence |
|---------|-----------|--------|----------|
| Shell/PowerShell incompatibility | 4 of 7 sessions | ~5-10 wasted tool calls per incident | [FR-0020 lifecycle](44937061) lines 27+: `&&` and `head` used in PowerShell; [GitHub-ready](7609d71f) subagent used `ls -la` on Windows |
| Sandbox permission blocking git writes | 2 of 7 sessions | ~15-20 wasted tool calls in worst case | [FR-0020 lifecycle](44937061) lines 39-59: 10+ attempts to `git add`/`git commit` before discovering `required_permissions: ["all"]` was needed |
| Subagent stalls/timeouts | 1 of 7 sessions | Complete loss of subagent work (~30% completed, rest had to be redone) | [GitHub-ready](7609d71f) subagent d54a3413: only 2 transcript entries, stalled after LICENSE + profiles/ creation |
| CI vs. local environment mismatch | 1 of 7 sessions | 3 commit-push-fail cycles | [FR-0020 lifecycle](44937061) lines 29-36: Python 3.11 f-string syntax error (PEP 701) passed locally on 3.12, failed in CI on 3.11 |
| Git repo boundary confusion | 2 of 7 sessions | 2-5 wasted commands | [FR-0020 lifecycle](44937061) lines 41-42: committed from workspace root instead of project subdirectory; `gh` hitting wrong repo from wrong cwd |

**2. Redundant/ineffective context retrieval**

- **CoS boilerplate reads**: Every CoS invocation reads 5 hub-state files + runs 3 reindex scripts. Across 5 CoS sessions: ~40 tool calls for hub-state refresh. Value exists for overlap detection, but a cached/incremental approach could cut this by ~60%.
- **Deep spec dependency chains**: The Developer subagent for [FR-0020](2d936a32) read 15+ files before its first edit (AGENTS.md, architect.md, developer.md, FR-0020, FR-0009, FR-0014, 7 source files, tests, deployment configs). Some reads (architect.md while in developer role) add marginal value.
- **Cross-session duplicate reads**: FR-0009 spec was read by the Architect subagent, Developer subagent, Reviewer subagent, and CI-fix parent — 4+ reads of the same unchanged file in one FR lifecycle.
- **Estimated read-to-use ratio**: Across the Developer session ([2d936a32](2d936a32)), approximately 12 of 15 initial reads directly influenced edits (~80%). The 3 unused reads (architect.md, FR-0014 full spec, deployment README) were "context insurance" that didn't change behavior.

**3. Low-value overhead**

- **Role preamble ceremony**: Every subagent starts by reading AGENTS.md + its role definition file. Constraints like "Developer does not modify specs" are self-enforced with no automated guard. The 2 reads per session × many sessions adds up, but the constraints DO shape behavior (Developer in [2d936a32](2d936a32) respected scope boundaries).
- **Index reindexing at CoS startup**: `hub-index.py`, `parking.py reindex`, `patterns.py reindex` are serial and blocking. If nothing changed since last run, this is pure waste. No incremental/dirty-checking mechanism exists.
- **Parking lot reflect**: Quick but surfaces actionable data in ~0% of observed sessions.

**4. What's working well**

- **Spec-driven structure produces high-quality specs**: The FR-0020 Architect subagent ([e2f3066d](44937061)) produced a remarkably thorough spec: 10 individually testable ACs, clear scope/out-of-scope, open questions with defaults. Quality comes directly from the template conventions and existing spec examples.
- **Todo tracking within sessions**: Both the Developer ([2d936a32](2d936a32)) and GitHub-readiness subagent used TodoWrite to track multi-step progress, providing clear structure.
- **Gate as hard constraint**: deploy-gate caught the AC-13 cross-reference issue in FR-0020's spec. Tests caught the `model_provider` Literal type restriction. These mechanical checks prevent silent failures.
- **CoS triage classification**: Accurate across all sessions — "park" for IDEA-0015, "promote-to-fr" for Bedrock migration and Fathom MCP, "chat + park" for effectiveness analysis. Overlap detection worked (ec4b75ec correctly linked IDEA-0001 to IDEA-0015).
- **Scope-change handoff**: When FR-0020 scope changed (Bedrock → LiteLLM), the parent correctly dispatched a new subagent to rework the spec rather than patching. This kept the spec coherent.

**5. Meta-architecture assessment**

- **Spec graph constrains behavior: YES.** FR-0020 lifecycle shows the full role cycle working end-to-end. The `depends_on` field is used but not deeply enforced (Developer doesn't verify dependency implementations before starting).
- **Role cycle is followed but partially ceremonial.** The Developer reads developer.md but constraints are self-enforced. No mechanism prevents a Developer from editing a spec. The Reviewer role naturally merged with Developer in practice ([FR-0020 lifecycle](44937061) dispatched a combined "Review and fix" subagent).
- **Escalation log: NOT USED.** Zero of 7 sessions wrote to or referenced an escalation log. The observability.md describes escalation patterns but they're aspirational, not operational.
- **Handoff artifacts: PARTIALLY PRODUCED.** Architect produces spec files (good). Developer produces code commits (good). But no structured Developer→Reviewer handoff note exists. Parent agents manually bridge: "dispatched Reviewer to catalog errors, then Developer to fix them."
- **This analysis was HARD to perform.** Raw JSONL transcripts are the only data source. No structured outcome annotations, no session metadata (success/failure, elapsed time, tool call count), no aggregation tooling. The architecture provides raw signals but zero aggregation layer.

## Value hypothesis

Based on concrete findings from the transcript analysis:

1. **Shell/permission error detection** could flag the "wrong shell syntax on Windows" and "missing sandbox permissions" patterns that account for **~20-30% of wasted tool calls** across sessions. A pre-flight check or session-start diagnostic would eliminate the trial-and-error discovery loop that cost ~15-20 tool calls in the worst observed case ([FR-0020 git commit struggle](44937061)).

2. **Context retrieval ROI tracking** could identify that ~20% of reads per session are "context insurance" that don't influence output. Over time, this data would inform which role-file reads to make mandatory vs. optional, potentially saving 3-5 reads per subagent invocation. Across a typical FR lifecycle (4+ subagent sessions), that's 12-20 saved reads.

3. **CoS startup optimization**: The 5-read + 3-script hub-state refresh is run ~5x per week. A dirty-checking mechanism (compare file mtimes or git hashes) could skip reindexing when nothing changed, saving ~60% of CoS startup overhead.

4. **Subagent stall detection**: The GitHub-readiness subagent stalled silently. An analysis tool that checks transcript growth rate could surface "subagent produced 2 entries in 26 minutes" as an anomaly, enabling faster intervention.

5. **Escalation log adoption metric**: The finding that 0/7 sessions used the escalation log is itself a finding only visible through systematic analysis. Monthly reports would reveal whether template components are actually used, driving informed deprecation/improvement decisions.

**Estimated compound value**: If run monthly, this tool would surface 2-3 actionable template improvements per cycle, each eliminating a friction point observed across multiple sessions. Over 6 months, this compounds into a meaningfully leaner template where agents spend less time on preamble and error recovery and more on productive work.

## Relationship to IDEA-0015

IDEA-0015 ("Template drift detection and back-propagation lifecycle") is complementary but distinct:
- **IDEA-0015**: "Is the project still conforming to the template?" (structural drift)
- **IDEA-0016**: "Is the template itself effective?" (outcome evaluation)

Both feed into template improvement, but from different angles. IDEA-0015 detects *whether* the template is followed; IDEA-0016 detects *whether following it produces good outcomes*. They could share infrastructure (a transcript parser) but serve different analytical goals.

## Derived IDEAs

Three concrete, high-ROI improvements were extracted from this analysis and parked as standalone IDEAs:

- **IDEA-0017**: Sandbox permission pre-flight check for git-write tasks — addresses the single most expensive failure pattern (~15-20 wasted tool calls per incident)
- **IDEA-0018**: Deprecate or automate escalation log — removes the zero-usage template component (0/7 sessions)
- **IDEA-0019**: CoS dirty-checking hub-state refresh optimization — eliminates ~60% of CoS startup overhead (~40 redundant tool calls across 5 sessions)

All three are template/hub-level improvements sized as S. Each references `derived_from: IDEA-0016`.

## Notes

### Raw findings by category

#### Error patterns (detailed)

**Shell/PowerShell incompatibility:**
- Agents default to bash/Unix conventions (`&&`, `head`, `ls -la`, `wc -l`) despite running in PowerShell on Windows.
- [FR-0020 lifecycle](44937061) line 27: `gh run view ... --log-failed 2>&1 | head -100` fails.
- [GitHub-ready subagent](7609d71f) line 3: `ls -la` fails, retried as `Get-ChildItem`.
- Root cause: System prompt says Windows/PowerShell but agents' training favors Unix syntax.

**Sandbox permission issues:**
- Git write operations (`git add`, `git commit`, `git push`) are blocked by the Cursor shell sandbox unless `required_permissions: ["all"]` is explicitly requested.
- [FR-0020 lifecycle](44937061) lines 39-59: The parent agent tried ~10 different approaches to commit before discovering the sandbox was the blocker: different quoting, `git commit -a`, `--no-verify`, Windows-style paths. Eventually `required_permissions: ["all"]` unlocked it.
- This is the single most expensive failure pattern observed — ~15-20 wasted tool calls in one session.

**Subagent stalls:**
- [GitHub-ready](7609d71f): Subagent d54a3413 received a 9-item work plan. Transcript shows only 2 entries (prompt + first response with todo list). On-disk evidence shows LICENSE created, profiles/ populated, template/AGENTS.md modified — but then work stopped with no error.
- Parent checked in at +26min (line 14) and +44min (line 20), confirmed stall, launched replacement subagent.
- No mechanism to detect or recover from stalled subagents automatically.

**CI/local environment mismatch:**
- [FR-0020](44937061) lines 29-36: `scripts/dispatch.py` used `f'{fr['id']}'` (nested f-string with same quote type) — valid in Python 3.12 (PEP 701) but SyntaxError on CI's Python 3.11.
- Developer subagent tested locally against 3.12, passed. CI ran 3.11, failed. Parent agent diagnosed and fixed directly.

#### Context retrieval patterns (detailed)

**Read sequences per role:**
- CoS: 5 hub-state files + cos command file + optional related ideas/FRs = 6-10 reads
- Architect: AGENTS.md + architect.md + dependent specs + source files + template = 8-15 reads
- Developer: AGENTS.md + developer.md + FR spec + dependent specs + source files + tests + deployment = 12-20 reads
- Reviewer: AGENTS.md + reviewer.md + FR spec + source diff + tests = 8-12 reads

**Files read most frequently across all sessions:**
1. `AGENTS.md` — read in every subagent session (7+ times across all transcripts)
2. `hub/FR-INDEX.json` — read in every CoS session (5 times)
3. `parking-lot/INDEX.md` — read in every CoS session (5 times)
4. `specs/FR-0009-extraction-agent.md` — read 4+ times (architect, developer, reviewer, CI fix)
5. Role definition files (`developer.md`, `architect.md`) — read per-session

#### Template component effectiveness scores

| Component | Used? | Constrains behavior? | Worth the overhead? |
|-----------|-------|---------------------|---------------------|
| Spec graph (FR→AC traceability) | Yes, consistently | Yes — ACs drive implementation | **YES** |
| Role definitions (architect/developer/reviewer) | Read every session | Partially — self-enforced only | Mixed — valuable constraints but no automation |
| Deploy-gate (hard exit criterion) | Yes, consistently | Yes — catches spec issues and missing tests | **YES** |
| Handoff protocols | Partially followed | Partially — spec handoffs work, chat handoffs improvised | Moderate |
| Observability/escalation conventions | Not used | No | **NO — aspirational overhead** |
| CoS triage protocol | Followed precisely | Yes — produces good classifications | **YES** |
| Parking lot cycle | Used end-to-end | Yes — IDEAs → FRs (IDEA-0002, 0003 promoted) | **YES** |
| Pattern catalog | Referenced but thin | Minimally — only 1 rejected pattern | Low signal so far |

#### Session outcomes summary

| Session | Type | Outcome | Tool calls (est.) | Key friction |
|---------|------|---------|-------------------|--------------|
| [CoS: GitHub-ready](7609d71f) | Multi-IDEA → publish | Partial (subagent stalled, replaced) | ~100+ | Subagent stall |
| [CoS: Bedrock/LiteLLM](44937061) | FR lifecycle end-to-end | Success (with CI friction) | ~150+ | PowerShell syntax, sandbox permissions, CI Python version |
| [CoS: Template drift](ec4b75ec) | Triage → park | Success | ~20 | None |
| [CoS: Fathom MCP](9fd24d3b) | Triage → dispatch → PR | Success (complex orchestration) | ~80+ | Dispatch lock management, uncommitted dev files |
| [Template fix: handoff commit](d78ffa56) | Targeted template edit | Success | ~25 | None (clean execution) |
| [CoS: Effectiveness analysis](e2370f79) | Triage → delegate | In progress | ~15 | None yet |
| [Dev: FR-0020 impl](2d936a32) | Developer implementation | Success | ~75 | Lint/format iterations |
