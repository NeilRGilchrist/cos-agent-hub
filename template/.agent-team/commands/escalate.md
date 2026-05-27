You are escalating to the human supervisor. Stop the work you were doing. Do not proceed past this until the human responds.

Read `.agent-team/escalation-matrix.md` to confirm this is something that should escalate to a human (not laterally to Architect or self-remediate).

Then emit exactly this block, filled in:

```
## ESCALATION
- FR(s): <FR-XXXX, or "n/a" if pre-spec>
- Role escalating: <Architect | Developer | Reviewer>
- Trigger: <one of the rows in the escalation matrix>
- Summary: <one paragraph: what's blocking, what you tried, what's at stake>
- What I tried: <bullets of attempts, if applicable>
- What I need from you: <a specific decision, a clarification, or an input -- be concrete>
```

Argument the user gave you: $ARGUMENTS

Also append a row to `.agent-team/escalation-log.md` using the helper script:

```
python3 scripts/escalation-log.py append --fr <FR-XXXX or n/a> --role <role> --trigger "<trigger>"
```

Once the human responds, update the resolution column:

```
python3 scripts/escalation-log.py resolve --fr <FR-XXXX> --resolution "<resolution>"
```
