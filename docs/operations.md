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

The account ID must correspond to an entry in `accounts.local.json5`.

The utility prints both the raw IMAP folder listing and extracted mailbox names.

## Cron

The intended deployment model is a cron job that runs the filter periodically.

Example:

```cron
*/5 * * * * /usr/bin/python3 /home/pi/mail-filter/mail_filter.py >> /home/pi/mail-filter/mail_filter.log 2>&1
```

This causes the filter to run every five minutes.

## Logging

The application logs to standard output.

A cron deployment can redirect that output to:

```text
mail_filter.log
```

Log entries include timestamps and account/rule context.

Examples of operational conditions that are logged include:

- connection
- folder validation warnings
- failed IMAP searches
- failed message fetches
- rule matches
- failed mailbox copies
- missing folder mappings
- move/delete actions
- no matching rule

## Log Rotation

The historical deployment configuration uses `logrotate`.

Example:

```text
/home/pi/mail-filter/mail_filter.log {
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

This is especially important for the planned UID and Gmail changes.

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
Gmail move behavior
       ↓
test
       ↓
commit
```

This keeps the live system reversible at each stage.
