# Handoff Protocols

Agents coordinate through artifacts, not chat. This document defines what each artifact must contain when one role hands off to another.

## Artifact: New or updated FR (Architect → Developer)

**Lives at:** `specs/FR-XXXX-<slug>.md`

**Required contents:**
- YAML frontmatter (see `specs/_template/FR-XXXX-template.md`)
- All acceptance criteria numbered and individually testable
- Out-of-scope section
- Changelog (if updated)

**Handoff signal:** Frontmatter `status: ready`, plus a message to the Developer naming the FR ID.

**What the Developer reads to start work:**
1. The FR file itself (full)
2. Anything in `depends_on` (transitively)
3. Relevant ADRs in `specs/decisions/`
4. `CLAUDE.md`

The Developer does not need to read other agents' conversation history.

## Artifact: Pull Request (Developer → Reviewer)

**Lives at:** GitHub/GitLab PR

**Required contents (in PR description):**
```
## FRs implemented
- FR-XXXX

## AC coverage
- AC-1: src/foo.py:bar()
- AC-2: src/foo.py:baz()

## Out of scope (per FR)
- <bulleted list from FR>

## Notes for Reviewer
- <anything non-obvious>
```

**Handoff signal:** PR marked `Ready for Review` (not draft), and CI passing the Developer-stage gates.

**What the Reviewer reads to start work:**
1. The PR diff
2. Every FR listed in the PR
3. Existing tests under `tests/`
4. `CLAUDE.md`

## Artifact: Review feedback (Reviewer → Developer)

**Lives at:** PR review (GitHub/GitLab native)

**Required contents:**
- `Request changes` review state (not just a comment)
- Each comment must reference an AC: e.g., "AC-2: input validation missing"
- Each comment must specify what would satisfy the Reviewer

**Handoff signal:** Review submitted with `Request changes` state.

## Artifact: Approval (Reviewer → merge gate)

**Lives at:** PR review

**Required contents:**
- `Approve` review state
- Confirmation in review body that:
  - All ACs have `@covers` annotations
  - Adversarial tests are present
  - Deploy gate is green

**Handoff signal:** PR shows approved + green CI. Merge is then a human or auto-merge gate decision.

## Artifact: Escalation (any role → Architect)

**Lives at:** PR comment OR a new file under `specs/_escalations/<date>-<slug>.md`

**Required contents:**
```markdown
## Escalation: <one-line summary>
- FR: FR-XXXX
- AC affected: AC-Y (if applicable)
- Role escalating: <role>
- Issue: <one paragraph>
- Interpretations considered: <bullet list>
- Blocking work on: <PR # or branch>
```

**Handoff signal:** A `[BLOCKED-ON-SPEC]` label on the PR or a notification mentioning the Architect agent.

## Artifact: Escalation (any role → Human)

See `.agent-team/escalation-matrix.md` — uses the structured `## ESCALATION` block format.

## What NOT to put in handoff artifacts

- ❌ Conversation history with the human ("the user said X")
- ❌ Reasoning about your own context window or token budget
- ❌ Apologies, hedging, or filler ("I tried my best to...")
- ❌ Anything specific to a previous session

The receiving agent has no memory of any prior session. Write artifacts as if for a stranger who has the role definition and `CLAUDE.md` and nothing else.
