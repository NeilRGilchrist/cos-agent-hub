# Observability Conventions

When five developers each run multiple agent crews, the bottleneck becomes "which crew needs me right now?" not "is any individual crew working?". This doc defines the conventions that make a status dashboard possible.

## Status fields on every PR

Every agent-driven PR must carry one of these GitHub/GitLab labels at all times:

| Label | Meaning | Owner |
|-------|---------|-------|
| `agent:dev-in-progress` | Developer is implementing | Developer |
| `agent:ready-for-review` | Awaiting Reviewer | Reviewer |
| `agent:review-in-progress` | Reviewer is testing | Reviewer |
| `agent:changes-requested` | Back to Developer | Developer |
| `agent:blocked-on-spec` | Escalated to Architect | Architect |
| `agent:blocked-on-human` | Escalated to human | Human |
| `agent:approved` | Ready to merge | Human (or auto-merge gate) |

When transitioning state, the agent **removes the old label and adds the new one in the same action**. Never leave a PR with two state labels.

## FR frontmatter status

FRs carry their own status in YAML frontmatter:

| Status | Meaning |
|--------|---------|
| `draft` | Architect is still writing |
| `ready` | Available for a Developer to pick up |
| `in-progress` | A Developer has started; PR exists |
| `in-review` | PR is awaiting/under review |
| `merged` | Implementation merged to main |
| `deprecated` | No longer applies; preserved for history |
| `blocked` | Cannot progress; see linked escalation |

## Escalation log

Every human-bound escalation appends a line to `.agent-team/escalation-log.md`:

```
| Date | FR | Role | Trigger | Resolution |
|------|----|----|---------|------------|
| 2026-04-28 | FR-0013 | Developer | AC ambiguity | Architect added AC-4 |
```

Read this weekly during your review cadence. Patterns reveal what's broken in the system: same FR escalating repeatedly = bad spec; same role escalating repeatedly = bad role definition; same trigger across many FRs = process gap.

## Suggested dashboard query (build later)

A minimal dashboard pulls:

1. All open PRs with `agent:*` labels, grouped by current state
2. Count of `agent:blocked-on-human` labels — your decision queue
3. Open FRs by status, with PR linkage
4. Escalations in the last 7 days, grouped by role and trigger

You can build this as a 100-line Python script against the GitHub API once you have ≥3 active agent crews. Don't build it before then.

## Agent self-reporting

At the start of every agent session, the agent should run:

```
python3 scripts/agent-status.py --fr FR-XXXX
```

This prints the current state of an FR (status, linked PRs, recent escalations) so the agent doesn't start work on something already in flight.

This is a simple guardrail against two parallel Developers picking up the same FR.
