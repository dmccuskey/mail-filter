# Mail Filter Operations

## Running the Filter

The main program is:

```bash
python3 mail_filter.py
```

The script loads `accounts.local.json5`, `folders.local.json5`, and `rules.local.json5` from the directory containing `mail_filter.py`, regardless of the current working directory.

On the Raspberry Pi deployment, this is:

```text
/home/pi/mail-filter
```

### Startup Rule Check

Before connecting to any account, the filter checks every account's rules for unknown match fields. If it finds any, it logs one `ERROR` line per unknown field and a summary line, processes no mail, and exits with status 1:

```text
[2026-09-23 16:09:44] [gmail-main] ERROR: rule #1 'delete 1800gotjunk to Archive' uses unknown match field 'from_contains'
[2026-09-23 16:09:44] ERROR: 1 unknown match field(s) in rules.local.json5; no mail processed (supported fields: docs/rule-reference.md)
```

All unknown fields in all accounts are reported in one run, so they can be fixed together. After editing `rules.local.json5`, run the filter once by hand (ideally with `DRY_RUN = True`) to confirm the check passes. See [Unknown Match Fields](rule-reference.md#unknown-match-fields).

## Dry Run

The script contains:

```python
DRY_RUN = False
```

Set this to:

```python
DRY_RUN = True
```

to test rule matching without performing move/delete side effects.

Dry-run mode is particularly useful when changing rules.

## Development Logging

The script also contains:

```python
DEV_LOGS = False
```

When enabled, additional diagnostic logging is emitted while matching messages.

This is intended for troubleshooting rather than normal operation.

## Folder Inspection

`list_folders.py` can inspect the mailboxes available on a configured account.

It uses the account information from `accounts.local.json5`, loaded from the directory containing `list_folders.py`, regardless of the current working directory.

Usage:

```bash
python list_folders.py gmail-main
```

The account ID must be a key in `accounts.local.json5`.

The utility prints both the raw IMAP folder listing and extracted mailbox names.

## Cron

The intended deployment model is a cron job that runs the filter periodically.

Example:

```cron
*/5 * * * * /usr/bin/python3 /home/pi/mail-filter/mail_filter.py >> /home/pi/mail-filter/mail_filter.log 2>&1
```

This causes the filter to run every five minutes.

If `rules.local.json5` has an unknown match field, every cron run logs the startup rule errors and exits with status 1 without processing mail, until the rules are fixed.

## Logging

The application logs to standard output.

A cron deployment can redirect that output to:

```text
mail_filter.log
```

Log entries include timestamps and account/rule context.

Examples of operational conditions that are logged include:

- connection
- unknown match fields found by the startup rule check
- folder validation warnings
- failed IMAP searches
- failed message fetches
- rule matches
- failed mailbox copies
- missing folder mappings
- move/trash/delete actions
- no matching rule

## Log Format

Every processed message produces exactly one line, written after its outcome is known, so a line never reports a destination the message did not reach.

```text
[timestamp] [account] MATCHED #uid RULE='rule name' SUBJECT='subject' <outcome><extras>
[timestamp] [account] NO_RULE #uid SUBJECT='subject'
```

- `[...]` is used only for the timestamp and the account ID.
- `MATCHED` means a rule, or `catch_all` (`RULE='<catch_all>'`), matched. `NO_RULE` means nothing matched and there is no catch-all.
- `#uid` is the message's IMAP UID. A UID is unique only within one account's mailbox, so combine it with the account when searching across accounts.
- `RULE` and `SUBJECT` are printed as-is; quotes inside them are not escaped.

The outcome is one of:

| Outcome | Meaning |
|---|---|
| `→ <mailbox>` | moved to that mailbox |
| `→ Trash (<mailbox>)` | trashed to that mailbox |
| `DELETED` | marked `\Deleted`; removed by the end-of-run `EXPUNGE` |
| `MARKED_READ` | `mark_read` was the only action; the message stays in `INBOX` |
| `NO_ACTION` | the rule has no actions; the message is unchanged |
| `SKIPPED: <reason>` | nothing was attempted; the message is unchanged |
| `FAILED: <reason>; <where the message was left>` | an IMAP step was refused |

The arrow `→` appears only when the message went somewhere. A `FAILED` outcome always ends by saying where the message was left, for example `left in INBOX` or `copied to 'GitHub', still in INBOX`, plus ` (marked read)` if `mark_read` had already been applied.

Extras follow the outcome:

- ` (mark_read)`: the message was also marked read. ` (mark_read FAILED: status=NO)` means the main action succeeded but `\Seen` could not be set.
- ` [DRY_RUN]`: `DRY_RUN` was on, so nothing on the server was changed. This is added to every per-message line in a dry run.

Examples:

```text
[2026-09-23 12:21:50] [gmail-main] MATCHED #16186 RULE='lifecare to Trash' SUBJECT='Your Member Discounts Are Here' → Trash (mail-filter-tests/alt-trash)
[2026-09-23 12:21:50] [gmail-main] MATCHED #16201 RULE='GitHub mail' SUBJECT='New issue' → GitHub (mark_read)
[2026-09-23 12:21:50] [gmail-main] MATCHED #16190 RULE='boulder theraputics no matching folder, error' SUBJECT='Spring newsletter' FAILED: copy to 'mail-filter-tests/MISSING-FOLDER' refused (status=NO); left in INBOX
[2026-09-23 12:21:50] [gmail-main] NO_RULE #16159 SUBJECT='🔉 David, big savings for 3 years! See Inside.'
```

Lines that are not about a single matched message keep an account-level form, for example:

```text
[2026-09-23 12:21:50] [gmail-main] ERROR: capability query failed; move and trash actions will be skipped
[2026-09-23 12:21:50] [gmail-main] Trash mailbox: '[Gmail]/Trash' (server \Trash)
[2026-09-23 12:21:50] [gmail-main] ERROR: fetch #16159 failed (status=NO)
[2026-09-23 12:21:50] [gmail-main] ERROR: rule #1 'GitHub mail' uses unknown match field 'from_contains'
```

The startup rule check ends with one line that has no account, because it covers the whole configuration:

```text
[2026-09-23 12:21:50] ERROR: 1 unknown match field(s) in rules.local.json5; no mail processed (supported fields: docs/rule-reference.md)
```

Rule configuration warnings are written as `Warning: RULE='name' ...`.

Useful searches:

```bash
grep MATCHED mail_filter.log              # every match
grep NO_RULE mail_filter.log              # unmatched messages
grep -E 'FAILED|ERROR' mail_filter.log    # anything that needs attention
grep 'SKIPPED' mail_filter.log            # matches that could not be acted on
grep "RULE='GitHub mail'" mail_filter.log # one rule
grep '#16186 ' mail_filter.log            # one message
```

No other line contains the word `MATCHED`.

## Log Rotation

The historical deployment configuration uses `logrotate`.

Example:

```text
/home/pi/mail-filter/mail_filter.log {
    su pi pi
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
```

This should be installed under:

```text
/etc/logrotate.d/mail-filter
```

Keep the `su pi pi` line (use your own user and group if not `pi`). It makes
logrotate rotate the file as the owning user rather than root. It is required
when `/home/pi/mail-filter` is group- or world-writable (for example, created
under a `umask` of `002`): without it, logrotate silently skips the file with
"parent directory has insecure permissions". Check with
`sudo logrotate -d /etc/logrotate.d/mail-filter`.

## Credentials

Credentials are stored in:

```text
accounts.local.json5
```

That file should remain outside Git.

Use:

```text
accounts.example.json5
```

for the shareable configuration structure.

## Live-System Safety

This program operates on real mail.

Before changing filtering behavior:

1. Commit the current known-good implementation.
2. Make one logical change.
3. Test with `DRY_RUN`.
4. Run against a controlled set of messages if possible.
5. Verify logs and resulting mail placement.
6. Commit the verified change.

Avoid combining unrelated IMAP behavior changes into a single checkpoint.

This is especially important for IMAP behavior changes such as the Gmail move and Trash handling in ADR 006.

## Recovery

The current deployed implementation should be treated as a known-good baseline.

Before behavior-changing work, create a Git checkpoint.

A useful development sequence is:

```text
known-good baseline
       ↓
commit
       ↓
UID migration
       ↓
test
       ↓
commit
       ↓
Gmail move and Trash behavior
       ↓
test
       ↓
commit
```

This keeps the live system reversible at each stage.
