---
description: Promote a parked IDEA into an active FR (in an existing project) or a new project. Updates bidirectional links.
argument-hint: "IDEA-NNNN"
---

You are promoting a parked idea out of the parking lot. The idea's content seeds either a new FR in an existing project or a brand-new project.

User's input: `$ARGUMENTS`

If `$ARGUMENTS` is empty or doesn't match `IDEA-NNNN`, ask the user which IDEA to promote and offer `python3 scripts/parking.py list` for a refresher.

## Step 1 — Read the idea

Run `python3 scripts/parking.py show $ARGUMENTS`. Read the description, value hypothesis, originating context, and tags.

## Step 2 — Confirm value hypothesis

If the value hypothesis is missing, vague, or stale (`(not articulated yet ...)` or similar), **stop and ask the user to articulate it before proceeding**. Promotion without a value hypothesis is a recipe for regret.

## Step 3 — Decide promotion target

Read `hub/projects.yaml`. Ask the user:

> Promote `$ARGUMENTS` to:
> 1. **Existing project** — pick from: `<list registered projects with their descriptions>`
> 2. **New project** — bootstrap a project named `<suggest a name from the IDEA title>`
> 3. **Cancel** — leave parked

Wait for the choice. Don't guess.

## Step 4a — Existing project

If the user picks an existing project:

1. Verify the project's `specs/` directory exists.
2. Allocate the next available `FR-NNNN` for that project (read `<project>/specs/INDEX.md` and bump highest+1, starting at FR-0001 if empty).
3. Use the IDEA's content to draft an FR:
   - `derived_from: $ARGUMENTS` in the frontmatter
   - `tags:` mirrors the IDEA's tags (the user can edit later)
   - Title from the IDEA
   - "Why" from the value hypothesis
   - "What" expanded from the description
   - Acceptance criteria — **leave as a placeholder asking the user/architect to fill in**. You are not Architect; you don't write ACs. (If the user wants Architect to handle ACs, recommend `/architect FR-NNNN` from inside that project after this command exits.)
4. Save the FR file at `<project-path>/specs/FR-NNNN-<slug>.md`.
5. Update the IDEA: `python3 scripts/parking.py promote $ARGUMENTS --to-fr <project>/FR-NNNN`
6. Run `python3 scripts/hub-index.py` to refresh the cross-project index.

Output:

> Created `<project>/specs/FR-NNNN-<slug>.md` (status: draft) — derived from $ARGUMENTS.
> Next: from inside `<project-path>`, run `/architect FR-NNNN` to fill in acceptance criteria.

## Step 4b — New project

If the user picks new project:

1. Confirm a project name (kebab-case slug) and stack (`python` / `node` / `none`).
2. Run: `scripts/bootstrap.sh <name> "<value hypothesis or one-paragraph from IDEA>" --stack <stack>`
3. Update the IDEA: `python3 scripts/parking.py promote $ARGUMENTS --to-project <name>`
4. After bootstrap, suggest the user `cd projects/<name>` and run `/architect <description>` for the first FR. The first FR should reference `derived_from: $ARGUMENTS`.

Output:

> Bootstrapped `projects/<name>` — derived from $ARGUMENTS.
> Next: cd into the project and run `/architect <one-line first FR description>`.

## Step 4c — Cancel

If the user cancels, do nothing. Don't change the IDEA's status.

## What you must NOT do

- ❌ Promote without a value hypothesis
- ❌ Write acceptance criteria for the new FR (Architect's job)
- ❌ Skip the bidirectional link update — both the IDEA and the FR/project must reference each other
