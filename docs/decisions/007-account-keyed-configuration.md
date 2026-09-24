# ADR 007: Account-Keyed Configuration Files

**Status:** Accepted

## Context

The three configuration files had different top-level shapes. `accounts.local.json5` held an `"accounts"` list with an `"id"` field in each entry, `folders.local.json5` wrapped its per-account maps in a `"folders"` object, and `rules.local.json5` was keyed directly by account ID. The mismatch made it easy to write one file in another's shape: a folders file without its wrapper crashed with a bare `KeyError`, and a rules file with a wrapper silently skipped every account.

## Decision

Make every configuration file an object keyed by account ID, with no wrapper:

```json5
{
  "<account id>": { /* settings for that account */ }
}
```

The account ID moves from an `"id"` field into the key. Accounts are processed in the key order of `accounts.local.json5`.

Reject the old shapes rather than accept both. Before connecting to any account, `mail_filter.py` checks for a top-level `"accounts"` list or `"folders"` wrapper, logs an error naming the file, processes no mail, and exits with status 1. A `"folders"` key is treated as the old wrapper only when no account uses that ID.

## Consequences

- All three files read the same way, and an account ID is a key everywhere it appears.
- There is one valid format, so a file in the wrong shape fails loudly instead of being half-understood.
- Existing deployments must edit `accounts.local.json5` and `folders.local.json5` once when upgrading; until then the filter refuses to run and leaves mail untouched.
