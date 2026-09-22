# ADR 004: Ordered First-Match Rule Evaluation

**Status:** Accepted

## Context

Mail filtering needs deterministic routing and a readable way to express exceptions before broader routing rules. A message may match more than one condition, so the engine must define which action controls the result.

## Decision

Evaluate an account's rules from top to bottom and apply only the first matching rule. Do not evaluate later rules for that message. If no normal rule matches, use the optional account-level `catch_all` action when present.

Within a rule:

- Different populated match fields are combined with AND semantics.
- Multiple values within a supported match field are combined with OR semantics.
- String comparisons are case-insensitive.
- Subjects and sender display names are MIME-decoded before matching.

## Consequences

- Rule ordering is part of configuration semantics and must be treated as significant.
- Specific exceptions should precede broader recipient-prefix rules and catch-all behavior.
- Each message receives at most one configured rule action per processing run.
- Rule behavior remains deterministic and reviewable without priority fields or conflict resolution.
