# Architectural Decision Records (ADRs)

Cross-FR architectural choices live here. An ADR is appropriate when a decision applies across multiple FRs and a Developer or Reviewer would otherwise have no canonical answer to "which way do we do X here?".

## When to write an ADR

- Choosing between two technologies that affect more than one FR (e.g., Postgres vs SQLite, REST vs gRPC)
- Establishing a convention that crosses FRs (error handling style, logging format, auth model)
- Recording a trade-off the team will need to remember six months from now

## When NOT to write an ADR

- Decisions scoped to a single FR — put them in the FR's Notes section instead
- Style preferences with no real cost either way — pick one and document in `CLAUDE.md`

## File format

`ADR-NNNN-<short-slug>.md`, sequentially numbered, never reused. Use the architecture skill (`engineering:architecture`) to draft them — it has a built-in template covering Context, Decision, Consequences, and Alternatives Considered.
