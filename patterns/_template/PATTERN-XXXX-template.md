---
id: PATTERN-XXXX
name: <one-line name>
status: proposed  # proposed | accepted | built | rejected
tags: []
created: YYYY-MM-DD
updated: YYYY-MM-DD
instances: []  # list of "<project>/FR-NNNN" — active FRs this pattern abstracts
ideas: []  # list of "IDEA-NNNN" — parked ideas this pattern subsumes
rejection_reason: null  # populated when status: rejected
---

# PATTERN-XXXX: <name>

## Description

What is this pattern? One paragraph at the shape level — what artifact (library, service, convention, scaffold) would be built if accepted.

## Compounding-value hypothesis

One paragraph. Why is the pattern greater than sum of its parts? Concrete claims only. "These are all about data" is not a hypothesis. Show the math: how many places does this currently get rebuilt, what does each rebuild cost, what does extraction cost, what does each future use save?

## Constituent signals

What evidence justifies extracting this pattern?

- IDEA-NNNN — <one-line: how this idea fits>
- <project>/FR-NNNN — <one-line: how this FR fits>

## Proposed shape

If accepted, what gets built? One paragraph. Include: artifact type (library/service/convention), language/stack, rough surface area, what it would replace.

## Alternatives considered

- **Don't build it.** Cost: <what continues to happen, e.g., each future project rebuilds the same shape>
- **Build it differently.** <If you considered another shape, why this one>

## Notes
