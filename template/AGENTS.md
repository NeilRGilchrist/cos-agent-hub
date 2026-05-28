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

## PHI hygiene (non-negotiable — applies if this project touches PHIPA-regulated data)

This template is provisioned by default for AlayaCare FDE engagements that may touch PHI. The hooks under `.agent-team/hooks/` and the wired `.claude/settings.json` enforce this defensively. If this specific project genuinely does not touch PHI, you may disable the hooks (edit `.claude/settings.json`) — but that decision is the human's, not the agent's.

While this block remains, every role enforces these:

- **No real client/patient names, IDs, addresses, DOBs, health card numbers, or SINs in chat, prompts, specs, code, tests, or commit messages.** Use ticket IDs (FDE-XXXX), FR IDs (FR-XXXX:AC-Y), and synthetic identifiers ("Patient A", "Provider 1") instead.
- **Test fixtures use synthetic data only.** Generated, not derived from real records. Fixtures live in `tests/fixtures/` and are reviewed for hygiene before commit.
- **Real-data testing happens in AlayaCare staging environments**, not via agent sessions. If you need to verify against real records, exit the agent session, run the verification manually in the staging tool, and bring back only the *outcome* (pass/fail/observation) — not the data.
- **Escalations cite IDs, not details.** "FR-0007:AC-2 ambiguous on retention behavior" — not "the issue with the record for [name]".
- **If you encounter real PHI in a file you're asked to edit**, stop, do not commit, flag to the human supervisor. The `phi_regex_check.py` hook will also catch obvious patterns and block the write — treat any block as a hard stop, not a thing to work around.

## Universal rules (all roles)

1. **Spec graph is the source of truth.** Every change traces to a Functional Requirement (FR) and one or more Acceptance Criteria (AC). No code changes without an FR.

2. **Stable IDs only.** FRs are `FR-XXXX` (4-digit, never reused). ACs are `AC-Y` (1-indexed within an FR). Tests reference them via `@covers FR-XXXX:AC-Y` comments.

3. **Artifacts, not chat.** When handing off to another role, write the handoff to a file (PR description, spec update, review comment). Never assume the next agent sees your conversation context.

4. **Escalate, don't guess.** If the spec is ambiguous, escalate to Architect rather than picking an interpretation. See `.agent-team/escalation-matrix.md`.

5. **Iteration budget: 3 cycles.** If a Dev↔Reviewer loop hits 3 round-trips on the same FR, force-escalate to human. The spec is probably wrong.

6. **Run the gate before claiming done.** `python3 scripts/deploy-gate.py` must pass before any role declares work complete.

7. **PHI never enters chat or commits.** Cited above; reinforced here so it lives next to the other universal rules.

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

- `.agent-team/**` — role definitions, escalation rules, and PHI hooks
- `.claude/settings.json` — hook wiring (PHI regex, spec validation, deploy-gate report)
- `scripts/deploy-gate.py` — the merge gate
- `scripts/index-specs.py` — the spec indexer
- This file (`AGENTS.md` / `CLAUDE.md`)

If a task seems to require editing these, escalate first.
