# ADR 003: Logical Folder Abstraction

**Status:** Accepted

## Context

IMAP mailbox names are provider- and account-specific. The same conceptual destination can be represented as `INBOX.Services.Payments` on one server and `Services/Payments` on another. Embedding those physical names directly in rules would couple routing policy to a particular mailbox layout.

## Decision

Have rules refer to stable logical folder keys, such as `services_payments`. Resolve each key through the configured folder map for the account currently being processed.

```text
rule action: services_payments
             |
             v
account folder map: INBOX.Services.Payments
```

Before normal processing, validate each configured mailbox by selecting it, then reselect `INBOX`. If a rule references a key without a mapping, log the problem and skip that message rather than using an unintended destination.

## Consequences

- Rules express classification intent rather than provider-specific mailbox syntax.
- A shared rule vocabulary can map to different physical mailbox names per account.
- Adding or changing a mailbox mapping is a configuration change.
- Folder configuration becomes required for an account to be processed safely.
