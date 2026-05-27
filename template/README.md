# Project README

This project was bootstrapped from the agent-team workspace hub. It runs a small team of specialized AI agents (Architect, Developer, Reviewer) coordinated through a spec graph and gated by CI.

## Mental model

```
        ┌─────────────┐
        │   Human     │  ← only sees true escalations
        └──────┬──────┘
               │
    ┌──────────┴──────────┐
    │      Architect       │  owns: specs, ambiguity arbitration
    └──────┬───────┬───────┘
           │       │
           ▼       ▼
    ┌──────────┐ ┌──────────┐
    │ Developer │ │ Reviewer │  ← lateral handoff via PR / spec graph
    └──────────┘ └──────────┘
```

Agents do not pass conversation history. They produce and consume **artifacts** (specs, code, test results) anchored to stable IDs (`FR-XXXX:AC-Y`).

## Slash commands

- `/architect <FR-XXXX | description>` — write or revise a spec. Includes a proactive overlap check against the workspace hub's parking lot, cross-project FR index, and pattern catalog.
- `/developer FR-XXXX` — implement against an FR
- `/reviewer FR-XXXX` — adversarially test a PR
- `/status [FR-XXXX]` — see what's in flight
- `/gate [--stage dev|review|full]` — run the merge gate locally
- `/escalate <reason>` — emit a structured human-bound escalation

The hub-level commands (`/cos`, `/park`, `/promote`, `/patterns`, `/hub-status`) live at the workspace hub, not inside this project. To use them, switch your Cowork session to the hub directory.

## Layout

- `specs/` — Functional Requirements (FRs) — the source of truth for what gets built
- `src/` — production code (Developer's territory)
- `tests/` — adversarial tests with `@covers FR-XXXX:AC-Y` annotations (Reviewer's territory)
- `scripts/` — `deploy-gate.py`, `index-specs.py`, `agent-status.py`
- `AGENTS.md` — canonical project working agreement (portable across harnesses)
- `CLAUDE.md` — thin pointer to `AGENTS.md` for Claude Code discovery
- `.agent-team/` — role definitions, escalation matrix, handoff protocols
- `.agent-team/commands/` — canonical command bodies shared by both harnesses
- `.claude/commands/` — Claude Code slash-command stubs
- `.cursor/commands/` and `.cursor/rules/` — Cursor command stubs and scoped rule metadata

## Project conventions

See `AGENTS.md` for the project's working agreement, including the tech stack, lint/test commands, glossary, and project-specific adjustments to universal rules. `CLAUDE.md` points to `AGENTS.md` for Claude Code.

## Working in Cursor vs Claude Code

- Claude Code discovers `CLAUDE.md`, which points at `AGENTS.md`.
- Cursor reads `AGENTS.md` directly and applies additional `.cursor/rules/*.mdc` files for scoped guidance.
- Both harnesses use the same command logic via `.agent-team/commands/`, with thin wrappers in `.claude/commands/` and `.cursor/commands/`.
- Local developer preferences go in `AGENTS.override.md` (gitignored) if needed.

## What NOT to customize per project

- Role taxonomy (Architect / Developer / Reviewer)
- Artifact contracts (how roles hand off)
- Escalation matrix structure
- FR frontmatter schema
- The role contracts in `.agent-team/roles/` and `.agent-team/commands/`

These are the workspace-wide standards. Diverging here breaks portability across projects and supervisor cognitive load.

## Span-of-control guidance

One human can sustainably supervise **3–5 agent crews** in parallel — not based on agent count, but on *aggregate decision rate*. If your agents escalate more than ~6 times per hour combined, tune the escalation thresholds tighter or invest in better specs upfront.
