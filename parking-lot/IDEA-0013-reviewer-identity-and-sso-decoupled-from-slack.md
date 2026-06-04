---
id: IDEA-0013
title: Reviewer identity and SSO decoupled from Slack
status: parked
tags:
- human-review
- auth
- sso
- identity
size: M
created: '2026-05-29'
updated: '2026-05-29'
last_reviewed: '2026-05-29'
promoted_to: null
pattern: null
archive_reason: null
---

# IDEA-0013: Reviewer identity and SSO decoupled from Slack

## Description

Replace YAML Slack `user_id` → tenant maps with a **proper identity layer**: OIDC/OAuth2 or corporate IdP, stable `reviewer_id`, and tenant RBAC enforced in FR-0018's `authorize` hook. Required before enterprise rollout and before the web console (IDEA-0012) trusts browser sessions.

## Originating context

Deferred from FR-0019. Originating FRs: ai-hub-poc FR-0018 (authorize hook), FR-0019 (Slack map).

## Value hypothesis

Unblocks multi-org review and web console without Slack user_id as sole principal.

## Notes

(none)
