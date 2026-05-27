# Escalations

Use this directory only when a PR comment isn't enough — most escalations should live on the PR or as comments tagging the relevant FR.

## When to put an escalation file here

- The escalation crosses multiple PRs or multiple FRs
- The escalation is to the human and you want a durable record outside of PR comments
- A Reviewer found a category of behavior the spec didn't anticipate and wants to propose a new FR or AC additions

## File format

`<YYYY-MM-DD>-<short-slug>.md`. Use the structured block defined in `.agent-team/handoff-protocols.md` (FR, AC affected, role escalating, issue, interpretations considered, blocking work on).

Once resolved, add a row to `.agent-team/escalation-log.md` and either delete the file or move it under a `resolved/` subdirectory if you want to preserve history.
