# CLAUDE.md — Project Working Agreement

This file is read by every agent at the start of every session. Keep it short. Anything project-specific that doesn't fit here belongs in a spec file under `specs/`.

> **⚠️ TEMPLATE — NOT YET CONFIGURED.** The sections below contain `<<REPLACE: …>>` markers. If you are an agent reading this and you see any of those markers, the project has not been bootstrapped yet. **Stop work** and ask the human to run `scripts/bootstrap.sh <project-name>` (from a fresh copy of the template) or to fill the markers in by hand. Do not invent project context to fill the gaps. Once every marker is gone, delete this warning block.

## What you are working on

<<REPLACE: ONE_PARAGRAPH_PROJECT_DESCRIPTION>>

Example (replace, then delete this line): "This repo implements the bidirectional EMR transformation layer for Helena. The goal is to map between agency EMR data shapes (AlayaCare native, PS Suite, custom) and our canonical CareEngagement → ServiceAuthorization → CareEpisode → Visit model."

## Your role

Before doing anything, identify which role you are operating in:

- **Architect** — see `.agent-team/roles/architect.md`
- **Developer** — see `.agent-team/roles/developer.md`
- **Reviewer** — see `.agent-team/roles/reviewer.md`

If the user has not told you which role, ask. Do not default — role determines what you are allowed to write to.

## Universal rules (all roles)

1. **Spec graph is the source of truth.** Every change traces to a Functional Requirement (FR) and one or more Acceptance Criteria (AC). No code changes without an FR.

2. **Stable IDs only.** FRs are `FR-XXXX` (4-digit, never reused). ACs are `AC-Y` (1-indexed within an FR). Tests reference them via `@covers FR-XXXX:AC-Y` comments.

3. **Artifacts, not chat.** When handing off to another role, write the handoff to a file (PR description, spec update, review comment). Never assume the next agent sees your conversation context.

4. **Escalate, don't guess.** If the spec is ambiguous, escalate to Architect rather than picking an interpretation. See `.agent-team/escalation-matrix.md`.

5. **Iteration budget: 3 cycles.** If a Dev↔Reviewer loop hits 3 round-trips on the same FR, force-escalate to human. The spec is probably wrong.

6. **Run the gate before claiming done.** `python3 scripts/deploy-gate.py` must pass before any role declares work complete.

## Project conventions

<<REPLACE: PROJECT_CONVENTIONS>>

Examples (replace with real values, then delete this block):
- Language: Python 3.11, type hints required
- Test runner: `pytest`
- Lint: `ruff check .` and `ruff format --check .`
- Commit style: Conventional Commits, scope = FR ID (e.g., `feat(FR-0013): add canonical visit mapper`)
- PRs must reference all FR IDs they touch in the title

## Glossary

<<REPLACE: PROJECT_GLOSSARY>>

Examples (replace with real terms, then delete this block):
- **Canonical model** — the internal data shape FRs map to/from
- **Adapter** — code that translates between an external EMR and the canonical model
- **CareEngagement** — the top-level container in our hierarchy

## Grounding documents

<<REPLACE: GROUNDING_DOCUMENTS>>

Examples (replace with real references, then delete this block):
- **Design doc**: `https://confluence.example.com/display/TEAM/Feature+Design`
- **Prior art repo**: `https://github.com/org/prior-implementation` — reference for data model patterns
- **API spec**: `https://api.example.com/docs` — the external API this project integrates with

## Files you should never edit without explicit human approval

- `.agent-team/**` — role definitions and escalation rules
- `scripts/deploy-gate.py` — the merge gate
- `scripts/index-specs.py` — the spec indexer
- This file (`CLAUDE.md`)

If a task seems to require editing these, escalate first.
