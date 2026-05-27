# Escalation Matrix

The whole point of this matrix: keep the human's decision rate below their cognitive bandwidth. Lateral handoffs between agents are cheap; human escalations are expensive. Route accordingly.

## Decision tree

| Situation | Route to | Why |
|-----------|----------|-----|
| Spec is ambiguous | Architect | Architect owns specs |
| Code doesn't satisfy AC | Developer | Developer owns implementation |
| AC has no test | Reviewer (self-remediate) | Reviewer owns coverage |
| Test reveals spec gap | Architect | Spec needs an AC, not a code fix |
| Code outside FR scope | Developer (remove it) | Scope drift; no spec change needed |
| Two ACs contradict | Architect | Spec internal inconsistency |
| FR contradicts user intent | **Human** | Product direction question |
| Cross-FR conflict | **Human via Architect** | Architect proposes resolution, human approves |
| Security / privacy / legal concern | **Human** | Always |
| Dev↔Reviewer loop hits 3 cycles | **Human** | Spec is probably wrong |
| Architect is being asked to override an existing ADR | **Human** | Architectural decisions need human sign-off |

## What "escalate to human" means in practice

The escalating agent **stops work** and emits a structured message:

```
## ESCALATION
- FR(s): FR-XXXX
- Role escalating: [Architect | Developer | Reviewer]
- Trigger: <one of the rows in the matrix above>
- Summary: <one paragraph>
- What I tried: <if applicable>
- What I need from you: <a specific decision or input>
```

This format is what your observability dashboard (see `.agent-team/observability.md`) keys off of.

## Iteration budgets

| Loop | Budget | Action on exceed |
|------|--------|------------------|
| Architect spec revision in response to Dev escalation | 2 revisions | Escalate to human — FR may need to be split |
| Dev↔Reviewer review cycles on one PR | 3 cycles | Escalate to human — likely spec issue |
| Reviewer self-remediation of coverage gaps | 2 attempts | Escalate to Architect — possible AC ambiguity |

## What NOT to escalate to a human

These are common mistakes that erode the human's bandwidth:

- ❌ "Should I name this variable `x` or `y`?" — Developer decides
- ❌ "This test takes 200ms, is that okay?" — Reviewer decides
- ❌ "I noticed code in another file that could be cleaner" — open a new FR or ignore
- ❌ "The Architect's spec uses different terminology than the codebase" — Reviewer flags it on the PR; Architect updates spec
- ❌ Anything you could resolve by re-reading the FR or `CLAUDE.md`

## Aggregate rate target

Across all agents under one human supervisor, target **≤ 6 escalations per hour combined**. If you're consistently over that:

1. Check whether iteration budgets are being respected (often the issue is agents skipping lateral handoff and going straight to human)
2. Check FR quality — vague ACs cause cascading escalations
3. Reduce the number of parallel agent crews until rate is sustainable
