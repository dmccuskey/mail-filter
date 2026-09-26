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

All three files have the same shape: an object keyed by account ID, with that account's settings as the value.

```json5
{
  "<account id>": { /* settings for that account */ }
}
```

An account ID in `accounts.local.json5` selects the entry with the same key in `folders.local.json5` and `rules.local.json5`. Accounts are processed in the order they appear in `accounts.local.json5`.

## Accounts

`accounts.local.json5` defines the IMAP accounts processed by the engine.

Schema:

```text
accounts.local.json5
└─ <account id>                 one entry per IMAP account
   ├─ imap_host      string
   ├─ username       string
   ├─ mail_enabled   boolean   optional, default true
   ├─ dry_run        boolean   optional, default false
   ├─ test_enabled   boolean   optional, default true
   └─ one of:
      ├─ password       string   the password itself
      └─ password_env   string   name of an environment variable holding it
```

Structure:

```json5
{
  "gmail-main": {
    "imap_host": "imap.gmail.com",
    "username": "username@gmail.com",
    "password": "password"
  },
  "work": {
    "imap_host": "imap.example.com",
    "username": "me@example.com",
    "password_env": "MAILFILTER_PW_WORK"
  }
}
```

### Fields

#### Account ID (the key)

Unique internal identifier for the account.

The ID is the key of the account's entry, and the same key selects the account's folder and rule configuration.

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

#### `password_env`

Name of an environment variable that holds the IMAP password, used instead of `password`. The password then lives outside `accounts.local.json5`, so that file holds no secrets and can be backed up or versioned like the other config files.

Each account sets exactly one of `password` or `password_env`, and accounts can mix the two styles. Before connecting to any account, the filter rejects an enabled account that sets both, sets neither, or names a variable that is unset or empty; it logs one `ERROR` line per problem, processes no mail, and exits with status 1. See [Credentials](operations.md#credentials) for supplying the variable under cron.

#### `mail_enabled`

Optional; defaults to `true`. Set it to `false` to skip the account without removing its entry. A disabled account is not connected to, and its password and rules are not checked at startup, so an account that is broken or only partly set up can be disabled without stopping the others. Each run logs:

```text
[2026-09-23 19:24:47] [mentalhijack-account] mail_enabled is false; skipping account
```

The value must be `true` or `false`. Anything else, including the string `"false"`, is rejected at startup: the filter logs an `ERROR`, processes no mail, and exits with status 1.

#### `dry_run`

Optional; defaults to `false`. Set it to `true` to run the account's rules without changing anything on its server: messages are fetched and matched, and each one's log line shows what would have happened, ending in ` [DRY_RUN]`. Nothing is moved, deleted, or marked read, and nothing is expunged. Each run logs:

```text
[2026-09-25 10:12:03] [gmail-main] dry_run is true; no changes will be made on the server
```

Use it for a new account, or after changing rules; remove it (or set it to `false`) to start filtering for real. It applies to one account, so the others keep filtering normally. See [Dry Run](operations.md#dry-run). Like `mail_enabled`, the value must be `true` or `false`.

#### `test_enabled`

Optional; defaults to `true`. Set it to `false` to leave the account out of the live IMAP tests (`imap_tests.py`), which create and remove test messages on the server; see [Testing](development.md#testing). The filter itself ignores this setting, and it is independent of `mail_enabled`: an account used only for testing can set `"mail_enabled": false`, and a personal account can set `"test_enabled": false`. Like `mail_enabled`, the value must be `true` or `false`.

## Folder Mapping

`folders.local.json5` maps logical folder keys to actual IMAP mailbox names.

Schema:

```text
folders.local.json5
└─ <account id>
   ├─ <folder key>: "<mailbox>"   any number; used by move
   └─ trash: "<mailbox>"          optional, reserved (see below)
```

Example:

```json5
{
  "aerospace-account": {
    "services_payments": "INBOX.Services.Payments",
    "services_to_review": "INBOX.Services.To Review"
  }
}
```

The left-hand value is the stable key used by rules.

The right-hand value is the mailbox's exact name on the IMAP server. This can differ from the name a mail client displays: it includes the server's hierarchy delimiter (usually `.` or `/`) and any prefix, such as `INBOX.` or `[Gmail]/`, and it is usually case-sensitive (IMAP only guarantees that `INBOX` is not). To get the exact names, run `python3 list_folders.py <account_id>` and copy them from its "Extracted mailbox names" section.

Mailbox names are never discovered from the server, so a wrong name is not corrected; it is caught at startup and logged as a warning (see [Folder Validation](#folder-validation)).

The key `trash` is reserved for the `trash` action and is optional. Trash is a role the server defines, so it is resolved differently from ordinary logical folders:

1. the account's `trash` mapping, if present;
2. otherwise the mailbox the server advertises with the IMAP `\Trash` attribute (for example `[Gmail]/Trash` or `INBOX.Trash`);
3. otherwise the `trash` action is skipped for that account.

Add a `trash` mapping only to override the server's Trash mailbox, or for a server that does not advertise `\Trash`. To see what a server advertises, run `python3 list_folders.py <account_id>` and look for `\Trash` in the attributes.

Because rules refer to folders only by key, each mailbox name is written in one place. If a mailbox is renamed on the server, or you want a different mailbox to receive a key's messages, change that one mapping; the rules that use the key stay as they are.

For example, after renaming `INBOX.Services.Payments` to `INBOX.Finance.Payments` on the server, update only the mapping:

```json5
"services_payments": "INBOX.Finance.Payments"
```

## Folder Validation

At startup, the script attempts to select each configured mailbox.

If a mailbox cannot be selected, a warning is logged.

The script then re-selects `INBOX` before processing messages.

A missing mapping referenced by a move rule is also logged and that message is skipped. A `trash` action whose Trash mailbox cannot be resolved is logged and skipped.

## Rules

Rules are stored under the account ID.

Schema:

```text
rules.local.json5
└─ <account id>
   ├─ rules: [ ... ]                 checked top to bottom; first match wins
   │  ├─ name       string           optional; shown in logs
   │  ├─ match      { <field>: string | [string, ...] }
   │  │                               fields are ANDed, values are ORed
   │  └─ do
   │     ├─ move       <folder key>
   │     ├─ trash      true | false
   │     ├─ delete     true | false
   │     └─ mark_read  true | false
   └─ catch_all                      optional; used only when no rule matches
      ├─ move       <folder key>
      ├─ trash      true | false
      ├─ delete     true | false
      └─ mark_read  true | false
```

`catch_all` takes the same actions as a rule's `do`, but has no `name` or `match`; it is logged as `RULE='<catch_all>'`. Match fields are listed in the [Rule Reference](rule-reference.md).

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

The supported match fields are listed in the [Rule Reference](rule-reference.md). Before connecting to any account, the filter checks every enabled account's rules; if any rule uses an unknown match field, has an empty `match`, or has a match field with no values, it logs an error for each problem (naming the account and rule), processes no mail, and exits with status 1. Use `catch_all` to act on every message no rule matches.

An optional `catch_all` sits in the account next to `rules`, and applies only when no rule matches:

```json5
{
  "aerospace-account": {
    "rules": [ /* ... */ ],
    "catch_all": {
      "move": "archive"
    }
  }
}
```

Like any `move`, `archive` here is a folder key from `folders.local.json5`.

## Upgrading from the Old Format

Earlier versions wrapped two of the files:

- `accounts.local.json5` held an `"accounts"` list, with an `"id"` field in each entry.
- `folders.local.json5` wrapped its accounts in a `"folders"` object.

To upgrade, remove the `"accounts"` list and make each account's `id` its key (dropping the `"id"` field), and remove the `"folders"` wrapper. `rules.local.json5` is unchanged.

An old-format file is never guessed at: `mail_filter.py` logs an `ERROR: ... uses the old format` line for each one, processes no mail, and exits with status 1. `list_folders.py` also refuses the old `accounts.local.json5` format.

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

Mail Filter includes its own copy of the `json5` Python package in `vendor/json5/`, so there is nothing to install. The bundled copy is always used, even when a `json5` package is installed, so every machine parses the configuration the same way. See [vendor/README.md](../vendor/README.md) for its version, license, and how to update it.
