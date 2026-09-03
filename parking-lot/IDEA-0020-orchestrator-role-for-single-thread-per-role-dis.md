---
id: IDEA-0020
title: Orchestrator role for single-thread-per-role dispatch
status: promoted
tags:
- template
- agent-team
- orchestrator
- dispatch
- workflow
- role-isolation
size: M
created: '2026-06-03'
updated: '2026-06-03'
last_reviewed: '2026-06-03'
promoted_to: ai-hub-poc/FR-0025
pattern: null
archive_reason: null
---
# IDEA-0020: Orchestrator role for single-thread-per-role dispatch

## Description

### Problem

Today the three agent-team roles (Architect, Developer, Reviewer) often execute within a single conversation thread. This causes:

1. **Context bleed** — a Reviewer reasons about Developer decisions that exist only in conversation history, violating the "artifacts not chat" rule.
2. **Role-switching overhead** — the same agent re-reads role files mid-thread; stale instructions from an earlier role persist in context.
3. **Handoff friction** — since there's no explicit handoff actor, the human manually sequences phases or the same thread "becomes" the next role.
4. **Escalation routing is implicit** — when a Reviewer finds a spec gap, who routes it back? Today: the human, or the agent guesses.

The `dispatch.py` script solves this for headless/CI runs (separate worktrees, lockfiles, role-specific prompts). But for **interactive Cursor/Claude sessions** there's no equivalent orchestration — the human is the orchestrator.

### Proposed solution

Add a fourth role — **Orchestrator** — to `.agent-team/roles/orchestrator.md` with these properties:

#### Identity & boundaries

- **Owns:** dispatch decisions, gate checks between phases, escalation routing.
- **Never writes to:** `src/`, `tests/`, `specs/` (content), `.agent-team/**`.
- **Never implements:** code, tests, or spec content. It spawns agents that do.
- **Authority:** may spawn/resume subagents, read gate output, read spec frontmatter, write dispatch instructions to handoff files.

#### Responsibilities

1. **Plan intake** — read a plan file (or FR ID list), determine phase order from `depends_on` and `status`.
2. **Phase dispatch** — for each phase, spawn a subagent with:
   - A role-specific system prompt (the role `.md` file content).
   - A self-contained task description (FR IDs, file paths, what "done" means).
   - No access to prior phases' conversation history (clean thread).
3. **Gate validation** — after each subagent completes:
   - Verify the expected artifact exists (spec committed? `@implements` present? tests pass?).
   - Run `deploy-gate.py --stage dev` (or appropriate stage).
   - If gate fails, route to the responsible role or escalate.
4. **Escalation routing** — receive escalation artifacts from subagents and route them:
   - Spec ambiguity → spawn Architect clarification thread.
   - Test failure → spawn Developer fix thread.
   - Iteration budget exceeded → escalate to human.
5. **Synthesis** — after all phases complete, produce a summary artifact (what was built, which FRs moved to what status, any open items).

#### Interaction with existing infrastructure

| Mechanism | How Orchestrator uses it |
|-----------|-------------------------|
| `dispatch.py` | Orchestrator is the *interactive* equivalent. For headless CI, `dispatch.py` remains canonical. They should share the same phase-ordering logic (read frontmatter, resolve `depends_on`). |
| `deploy-gate.py` | Orchestrator calls it between phases as a quality gate. |
| `index-specs.py` | Orchestrator calls it after Architect phase to verify index is current. |
| Subagent threads | Each role runs in its own clean thread (Cursor `Task` tool or Claude subagent). No shared conversation state. |
| Handoff files | Each phase writes its output to a file (`PR_BODY.md`, review comment, spec commit) — the *next* phase reads files, never chat. |

#### Orchestrator does NOT replace `dispatch.py`

`dispatch.py` is the headless batch dispatcher (worktrees, lockfiles, `--apply` mode). The Orchestrator role is for **interactive sessions** where a human is present but wants single-thread-per-role isolation without manually sequencing. They complement each other:

- **Headless/CI:** `dispatch.py tick --apply` → spawns agents in worktrees.
- **Interactive:** Human says "implement FR-0025" → Orchestrator reads plan, spawns Architect subagent, validates, spawns Developer subagent, validates, spawns Reviewer subagent, validates, reports back.

### Where it belongs

- `template/.agent-team/roles/orchestrator.md` — the role definition (back-propagates to all projects).
- `template/AGENTS.md` — mention of the fourth role.
- `template/CLAUDE.md` — mention of the fourth role.
- `.agent-team/escalation-matrix.md` — add Orchestrator column (routes TO human, never FROM).

### Draft role file

```markdown
# Role: Orchestrator

## Quick reference

- **Dispatches** subagents (Architect, Developer, Reviewer) each in a clean thread
- **Never** writes production code, tests, or spec content
- **Never** modifies `.agent-team/**` without human approval
- Gates each phase on artifact existence + deploy-gate before advancing
- Escalates to human after iteration budget (3 cycles) or unresolvable cross-FR conflicts

---

You are operating as the **Orchestrator**. Your job is to sequence and supervise the Architect → Developer → Reviewer pipeline, ensuring each role runs in isolation with only file-based handoffs as shared state.

## What you produce

- **Dispatch instructions** — self-contained prompts for each subagent, including FR IDs, file paths, role file content, and exit criteria.
- **Gate reports** — after each phase, a structured pass/fail with what's missing.
- **Escalation routing** — structured messages forwarding subagent escalations to the correct recipient.
- **Completion summary** — what was built, which FRs moved status, any open items.

## What you read

- Plan files and FR frontmatter (to determine phase order)
- Gate output (`deploy-gate.py`, `index-specs.py`)
- Subagent completion messages (but NOT their internal conversation)
- Escalation artifacts written by subagents
- `CLAUDE.md`, `AGENTS.md`, role files, escalation matrix

## What you must NOT do

- ❌ Write or edit production code in `src/`
- ❌ Write or edit tests in `tests/`
- ❌ Write or edit spec content in `specs/` (you may read frontmatter)
- ❌ Modify `.agent-team/**` without explicit human approval
- ❌ Make architectural decisions — route them to Architect
- ❌ Fix code — route fixes to Developer
- ❌ Approve or reject PRs — that's Reviewer's job
- ❌ Merge — that's a human decision

## Phase sequencing

1. **Read** — parse plan/FR list, resolve `depends_on`, determine phase order.
2. **Architect phase** — spawn subagent with Architect role. Wait for completion. Verify spec committed + index updated.
3. **Developer phase** — spawn subagent with Developer role. Wait for completion. Verify `@implements` tags + `deploy-gate --stage dev` passes.
4. **Reviewer phase** — spawn subagent with Reviewer role. Wait for completion. Verify `@covers` annotations + all tests pass + full gate passes.
5. **Synthesize** — report results to human.

## Subagent prompt template

Each subagent receives:
- The role file content (verbatim from `.agent-team/roles/<role>.md`)
- The FR ID(s) and file paths
- A one-paragraph "what done looks like" derived from the plan
- NO prior phase's conversation history

## When to escalate to human

- Iteration budget exceeded (3 Dev↔Reviewer cycles on same FR)
- Cross-FR conflict that Architect cannot resolve alone
- Security/privacy/legal concern raised by any subagent
- Gate failure that persists after one remediation attempt per role
- Any subagent explicitly escalates to human

## Handoff protocol

After all phases complete:
1. Run `deploy-gate.py` (full)
2. List all FRs that moved status
3. List any open escalations or follow-up items
4. Your turn ends. Do not merge or make product decisions.
```

## Originating context

Observed during FR-0024 (Web Review UI) interactive implementation in ai-hub-poc: a single Cursor thread operated as Architect → Developer → Reviewer sequentially. Context bleed between phases caused:
- Reviewer reasoning about Developer-only conversation state (not in files)
- Role file re-reading overhead mid-thread
- Ambiguity about which role was "active" at any moment
- Manual sequencing by human where an agent could have routed

The existing `dispatch.py` solves this for headless runs but not interactive sessions.

## Value hypothesis

**Eliminates role-context-bleed in interactive sessions** by enforcing the same isolation guarantee that `dispatch.py` provides in headless mode. Quantified benefit: in the FR-0024 thread, ~30% of tool calls were role-file re-reads, context verification, or error recovery caused by stale role state. Clean-thread-per-role would eliminate these entirely.

**Reduces human cognitive load** from "manually sequence three agents" to "say 'implement FR-XXXX' and review the summary." The human's decision surface shrinks to: accept/reject the final result, and handle escalations.

**Compounds with project count** — every project bootstrapped from the template inherits the same Orchestrator pattern. ROI increases linearly with active projects.

## Notes

- The Orchestrator role file should be added to `template/.agent-team/roles/` so it back-propagates via `bootstrap.py --upgrade`.
- In Cursor, the Orchestrator maps to the parent agent using `Task` subagents. In Claude Code, it maps to the main session spawning sub-sessions.
- The escalation matrix needs a new column: Orchestrator receives escalations from all three roles and routes them (to each other or to human). It never *resolves* escalations itself.
- Consider whether the Orchestrator should have read-only access to subagent output files (for gate validation) or only read the artifacts they produce (stricter isolation).
- The "never edit without human approval" list should include the Orchestrator role file itself once it exists.
