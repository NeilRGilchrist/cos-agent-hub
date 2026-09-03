---
id: IDEA-0012
title: Web-hosted human review console (FR-0017/FR-0018 client)
status: parked
tags:
- human-review
- web-ui
- hitl
- slack-follow-on
- review-surface
- claude-enterprise
size: M
created: '2026-05-29'
updated: '2026-08-11'
last_reviewed: '2026-08-11'
promoted_to: null
pattern: null
archive_reason: null
---

# IDEA-0012: Web-hosted human review console (FR-0017/FR-0018 client)

## Description

When the Slack client (FR-0019) strains under bulk triage, rich attribute editing, or graph-context review, add a **web-hosted console** that calls the same FR-0017 list/detail APIs and FR-0018 `apply_review_event` path. System of record stays the graph + `ReviewEvent` audit; the web app is an alternate client only.

## Originating context

Deferred from FR-0019 planning 2026-05-29. Originating FRs: ai-hub-poc FR-0017, FR-0018, FR-0019.

## Value hypothesis

When Slack modals strain, a thin web UI reuses the same read/write APIs without re-specifying graph semantics.

## Notes

### 2026-08-11 — Third surface option: enterprise Claude (via CoS triage)

Org-wide enterprise Claude rollout adds a **third candidate review-queue / knowledge-hub surface** alongside the web-hosted console (this IDEA) and Slack (FR-0019): the Claude enterprise client driven over the KB Query MCP (FR-0046 / FR-0054). This reframes the open "which surface" question rather than duplicating it — the hub is already Claude Code / Cursor-first by design, and FR-0054 already demoed the KB Query MCP inside Claude Code.

Trade-off vs. this web console: no hosted web app to build or operate, and more flexibility than Slack modals — but it inherits the MCP's safety posture, so it is only as trustworthy as that server.

Couplings surfaced in the same triage session:

- **MCP query guardrails** (→ new FR in ai-hub-poc): an unbounded Cypher query through the `ai-hub-kb` MCP exhausted host memory and crashed a machine. A bounded, trustworthy MCP is a prerequisite for adopting this surface.
- **Email prompt-injection assessment** (→ architect spike in ai-hub-poc): email-derived content flows into the extraction LLM and then the KG; injected content would surface through whatever review UI is chosen, including this one.
- **Identity/SSO** — see IDEA-0013 (→ FR-0052 tenant RBAC); the enterprise-Claude auth story changes the reviewer-identity picture.

Decision on which surface to invest in should still be driven by IDEA-0014's review-strain metrics.
