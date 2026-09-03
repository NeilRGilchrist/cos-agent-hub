---
id: IDEA-0030
title: Dispatch preflight should verify model gateway reachability before spawning
  agents
status: archived
tags:
- dispatch
- control-plane
- preflight
- ai-hub-poc
- dx
size: S
created: '2026-08-12'
updated: '2026-08-12'
last_reviewed: '2026-08-12'
promoted_to: null
pattern: null
archive_reason: Withdrawn by owner 2026-08-12. VPN is connected as a matter of course,
  so an unreachable gateway is a rare one-off rather than a recurring failure mode;
  a preflight probe would guard against a condition that essentially never holds.
  The residual signal (a gateway failure reporting terminal_reason=completed) is already
  in scope for FR-0058 AC-5, which requires distinguishing mechanical from semantic
  phase failures.
---
# IDEA-0030: Dispatch preflight should verify model gateway reachability before spawning agents

## Description

(no description provided)

## Originating context

Surfaced 2026-08-12 attempting dispatch.py maintain FR-0058. The spawned agent retried 10 times with exponential backoff over ~6 minutes, then exited is_error=true with terminal_reason=completed and zero tokens. Root cause was environmental: ANTHROPIC_BASE_URL in ~/.claude/settings.json points at litellm.can1.main.dev.alayacare.net, which resolves to RFC1918 10.49.x.x and needs VPN. No VPN adapter was up.

## Value hypothesis

A sub-second TCP reachability probe before spawn converts a 6-minute silent burn plus a misleading terminal_reason=completed into an immediate actionable message. Directly serves FR-0058 AC-5, which requires distinguishing mechanical from semantic failure; a gateway-unreachable exit is the purest mechanical failure and today it was indistinguishable from a clean no-op.

## Notes

(none)
