# Mail Filter Documentation

New to Mail Filter? Start with the [Quick Start](../README.md#quick-start) in the main README.

## Start

- [Quick Start](../README.md#quick-start): one account, one rule, running on a schedule
- [Installation](installation.md): macOS, Linux and Raspberry Pi, and Windows, including scheduling and updating

## Use

- [Configuration](configuration.md): the accounts, folders, and rules files, and every account setting
- [Rule Reference](rule-reference.md): match fields, match semantics, actions, rule order, and the catch-all
- [Operations](operations.md): running the filter, dry runs, folder inspection, cron, logging and the log format, log rotation, credentials, and recovery

## Internals

- [Architecture](architecture.md): system structure, processing flow, and the safety model
- [IMAP Notes](imap-notes.md): IMAP behavior, Gmail, EXPUNGE and sequence numbers, and the move to UIDs
- [Architecture Decisions](decisions/): the project's decision records (ADRs)

## Contribute

- [Development](development.md): the current baseline, possible future changes, testing (unit tests, live IMAP tests, recorded replies), and the branch workflow
- Planned work is tracked in [GitHub issues](https://github.com/dmccuskey/mail-filter/issues)

## Project Structure

```text
mail-filter/
├── mail_filter.py            # the filter
├── list_folders.py           # lists an account's mailboxes
├── imap_tests.py             # live IMAP tests
├── accounts.example.json5
├── accounts.local.json5      # you create (gitignored)
├── folders.example.json5
├── folders.local.json5       # you create (gitignored)
├── rules.example.json5
├── rules.local.json5         # you create (gitignored)
├── vendor/
│   ├── README.md             # bundled packages: versions, licenses
│   └── json5/                # JSON5 parser (Apache-2.0)
├── docs/
│   ├── README.md             # this page
│   ├── installation.md
│   ├── configuration.md
│   ├── rule-reference.md
│   ├── operations.md
│   ├── architecture.md
│   ├── imap-notes.md
│   ├── development.md
│   └── decisions/            # ADRs
├── tests/
│   ├── test_mail_filter.py
│   ├── test_live_imap.py
│   ├── test_recorded_imap.py     # replays recorded server replies
│   ├── imap_recording.py
│   └── fixtures/imap/            # recordings (imap_tests.py --record)
├── LICENSE
└── README.md
```
