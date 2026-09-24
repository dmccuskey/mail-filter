# ADR 002: Configuration-Driven Filtering and Configuration Organization

**Status:** Accepted

## Context

Mail-routing policy changes more often than the filtering engine. Accounts require connection details, folders vary by IMAP server, and rules express the desired classification and action. Configuration must also keep live credentials out of version control while documenting the required structure.

The preferred conceptual organization is one configuration file per server or account—for example, `gmail.config` containing that account's connection details, folders, and rules. Keeping everything for an account together is easier to inspect conceptually.

## Decision

Keep filtering policy in JSON5 configuration rather than hard-code it in the Python engine. The engine provides the matching and action mechanisms; configuration specifies the accounts, folder mappings, and routing policy.

For the current multi-account implementation, use three consistently named configuration files:

```text
accounts.local.json5
folders.local.json5
rules.local.json5
```

Account IDs join the account entry to its folder mappings and rules. [ADR 007](007-account-keyed-configuration.md) makes each file an object keyed by account ID. This organization was chosen because loading and passing multiple account configurations was simpler and less unwieldy than managing a collection of per-account files.

Commit corresponding `*.example.json5` files as templates. Keep live `*.local.json5` files out of Git.

## Consequences

- Routing changes can normally be made without modifying application code.
- Accounts, physical mailbox mappings, and filtering policy stay separately legible.
- Live credentials and deployment-specific values remain local, while example files document the configuration contract.
- Understanding one account currently requires following its ID across the three configuration files.

## Notes

The three-file arrangement is a current implementation choice, not a claim that it is the permanent or universally preferred configuration model. A future configuration-loader redesign may revisit the preferred per-server/account-file model when it offers a clearer operational fit.
