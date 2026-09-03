---
id: IDEA-0034
title: 'Reverse leg: harvest project fixes back into template, with AGENTS.md core/overlay split'
status: parked
tags:
- tooling
- template
- hub
- workflow
- drift
size: L
created: '2026-09-03'
updated: '2026-09-03'
last_reviewed: '2026-09-03'
promoted_to: null
pattern: null
archive_reason: null
---

# IDEA-0034: Reverse leg: harvest project fixes back into template, with AGENTS.md core/overlay split

## Description

Close the loop that `bootstrap.py --upgrade` only half-closes. Today template changes flow *down* to projects, but a fix or enhancement discovered *inside* a project (a hook, a dispatcher edge case, a skill, a working-agreement rule) has no path back to `template/`. The result is the same fix being rediscovered and re-applied per project, and — worse — a project-local fix being silently clobbered by the next `--upgrade` because upgrade treats template as canonical and does whole-file overwrite.

The idea has four behaviour-level parts:

1. **Capture in-project, no context switch.** A project-level `/upstream <path> "<reason>"` command that any role may run. It appends `{path, reason, commit, flagged}` to `.agent-team/upstream-queue.yaml` and adds a `Hub-Promote: <path>` commit trailer. It writes only to the project's own repo, so it does not violate the control-plane deny list — the agent tags the fix as hub-bound and keeps working.
2. **Harvest at the hub.** `scripts/harvest.py` + `/harvest [project]`. Walks `hub/projects.yaml`, reads each project's queue (with `git log --grep=Hub-Promote` as a backstop), and shows the *reverse* diff (project → template) for each flagged path. Applies at **hunk** level, not whole-file, because a project's infra file carries the generic fix *and* project-specific bits. On confirm, writes to `template/`, records `Harvested-From: <project>@<sha>` provenance, and marks the queue entry `harvested`. Propose-and-confirm, same as `/promote`. A `--scan` mode diffs all `INFRA_DIRS` paths across all projects against template to catch unflagged fixes; this doubles as the drift report `/hub-status` currently lacks.
3. **Fan-out.** `bootstrap.py --upgrade --all` over `projects.yaml`, plus a template hash stamp written per project so `/hub-status` can report "project X: N infra files behind."
4. **AGENTS.md as a rendered projection.** Split into `.agent-team/agents-core.md` (hub-owned, synced by `--upgrade`, harvestable) and `.agent-team/agents-project.md` (project-owned, never harvested). `AGENTS.md` becomes generated from the two, mirroring what `generate_commands` already does for `.claude/commands/` and `.cursor/commands/`. Existing `AGENTS.override.md` (gitignored) stays as the per-developer third tier: hub core → project overlay → dev override.

## Originating context

Repeatedly fixing the same script/skill defect in one project, then hitting it again in another because the fix only existed in the first repo. Investigation of the hub on 2026-09-03 found that `--upgrade` (downstream) exists and is documented in GETTING-STARTED.md and CONTRIBUTING.md, but there is no upstream mechanism at all. Archived IDEA-0001 had already flagged the tension as its open question #1 ("one canonical dispatcher, or per-project copies that drift?") and deferred it. The hub `CLAUDE.md` marks `template/**` as human-approval-only, which is *why* fixes stop at the project boundary — agents cannot push them up — so the reverse leg has to be a hub-level, propose-and-confirm command rather than a loosening of the deny list.

The core/overlay decision was argued out: marker regions (`<!-- hub:core -->` blocks inside AGENTS.md) were considered and rejected. A file split (a) makes harvest a single code path — `agents-core.md` is just another infra file, no region parsing, no ambiguity about whether an in-block edit is a fix or a local override; (b) is enforceable at the path level via `settings.json` denies and maintainer `owns:` footprints, which markers are not; (c) matches the existing `generate_commands` precedent and the "docs as generated projections over the spec graph" direction from the lockstep work.

## Value hypothesis

Reduces cross-project maintenance from O(projects) manual re-fixes per defect to one `/upstream` tag at discovery time plus one `/harvest` + `--upgrade --all` at the hub — and eliminates the class of bug where `--upgrade` overwrites a project-local fix that was never promoted.

## Notes

**Sequencing when promoted (rough phases):**

- Phase 1 — `/upstream` command + queue file + commit trailer. Smallest change, immediately stops fixes from being forgotten. Can ship alone.
- Phase 2 — `harvest.py` with hunk-level apply (shell out to `git diff --no-index` + `git apply` per hunk, or `git apply --3way` onto `template/`) and `--scan` drift mode. Extend `/hub-status` to surface drift.
- Phase 3 — `--upgrade --all` + per-project template stamp.
- Phase 4 — AGENTS.md projection split. Migration: existing projects' `AGENTS.md` content moves into `agents-project.md` verbatim; `agents-core.md` seeded from `template/AGENTS.md` minus `<<REPLACE>>` blocks. Add a generated banner pointing at `agents-project.md`, and a `deploy-gate` check that `AGENTS.md == render(core, project)` — same shape as `spec_validate_if_changed.py`.

**Ordering rule the tooling should enforce (or at least warn on):** harvest before fan-out. Running `--upgrade` on a project with a non-empty `upstream-queue.yaml` should refuse or warn, otherwise it clobbers exactly the fix that was meant to be promoted.

**Open questions:**

1. Should `--scan` (unflagged drift) auto-populate the queue as `candidate` entries, or only report? Leaning report-only to keep propose-and-confirm intact.
2. `harvest.py` and `bootstrap.py` both touch `template/`, which is human-approval-only per hub `CLAUDE.md`. Does harvest need its own entry in the "never edit without approval" list, or is the interactive confirm sufficient?
3. Skills: `.claude/skills/` already syncs down via `INFRA_DIRS` (`.claude` is included). Does a project-tuned `SKILL.md` need the same core/overlay treatment, or is directory-level ownership (template-owned vs project-owned skill dirs) enough?
4. Related: IDEA-0001 (archived — this resolves its deferred question #1), IDEA-0003 (reconcile-merged fetch ordering — any harvested `dispatch.py` change should land after that).
