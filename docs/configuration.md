# Mail Filter Configuration

Mail Filter uses JSON5 configuration files so comments and trailing commas can be used.

The live configuration is intentionally separated from the example configuration and should remain outside Git.

## Configuration Files

```text
accounts.example.json5
accounts.local.json5

folders.example.json5
folders.local.json5

rules.example.json5
rules.local.json5
```

The `.local.json5` files contain the actual deployment configuration.

The `.local.json5` files must be located in the same directory as `mail_filter.py` and `list_folders.py`; both scripts resolve them relative to their own location.

## Accounts

`accounts.local.json5` defines the IMAP accounts processed by the engine.

Structure:

```json5
{
  "accounts": [
    {
      "id": "gmail-main",
      "imap_host": "imap.gmail.com",
      "username": "username@gmail.com",
      "password": "password"
    }
  ]
}
```

### Fields

#### `id`

Unique internal identifier for the account.

The ID is also used to select the account's folder and rule configuration.

Examples:

```text
gmail-main
aerospace-account
fastmail-main
```

#### `imap_host`

Hostname of the IMAP server.

#### `username`

IMAP login username.

#### `password`

IMAP password or application-specific password.

Real credentials belong only in the local configuration.

## Folder Mapping

`folders.local.json5` maps logical folder keys to actual IMAP mailbox names.

Example:

```json5
{
  "folders": {
    "aerospace-account": {
      "services_payments": "INBOX.Services.Payments",
      "services_to_review": "INBOX.Services.To Review"
    }
  }
}
```

The left-hand value is the stable key used by rules.

The right-hand value is the actual mailbox name understood by the IMAP server.

The key `trash` is reserved for the `trash` action and is optional. Trash is a role the server defines, so it is resolved differently from ordinary logical folders:

1. the account's `trash` mapping, if present;
2. otherwise the mailbox the server advertises with the IMAP `\Trash` attribute (for example `[Gmail]/Trash` or `INBOX.Trash`);
3. otherwise the `trash` action is skipped for that account.

Add a `trash` mapping only to override the server's Trash mailbox, or for a server that does not advertise `\Trash`. To see what a server advertises, run `python3 list_folders.py <account_id>` and look for `\Trash` in the attributes. Ordinary logical folders are never discovered from the server.

This abstraction allows the same rule vocabulary to work with different mailbox naming conventions.

For example:

```text
services_payments
        ↓
INBOX.Services.Payments
```

A different account can map the same key differently.

## Folder Validation

At startup, the script attempts to select each configured mailbox.

If a mailbox cannot be selected, a warning is logged.

The script then re-selects `INBOX` before processing messages.

A missing mapping referenced by a move rule is also logged and that message is skipped. A `trash` action whose Trash mailbox cannot be resolved is logged and skipped.

## Rules

Rules are stored under the account ID.

Example:

```json5
{
  "aerospace-account": {
    "rules": [
      {
        "name": "Boulder Parks Auto Renewal",
        "match": {
          "to_local_is": ["my-boulderparknrecs"],
          "subject_contains": ["membership auto renewal"]
        },
        "do": {
          "move": "services_payments"
        }
      }
    ]
  }
}
```

The supported match fields are listed in the [Rule Reference](rule-reference.md). Before connecting to any account, the filter checks every account's rules; if any rule uses an unknown match field, it logs an error for each one (naming the account, rule, and field), processes no mail, and exits with status 1.

An optional `catch_all` may follow the rules:

```json5
{
  "catch_all": {
    "move": "catch_all"
  }
}
```

## Local vs Example Configuration

Example files document structure without containing real credentials or deployment-specific secrets.

Local files contain the actual runtime configuration and should remain gitignored.

The separation provides:

- safe source control
- readable configuration templates
- reproducible configuration structure
- protection of account credentials

## JSON5

JSON5 is used instead of strict JSON to allow configuration comments and trailing commas.

The script attempts to use the `json5` Python package and falls back to the standard JSON parser if the package is unavailable.

The actual configuration files currently rely on JSON5 syntax, so JSON5 should be considered the intended format.
