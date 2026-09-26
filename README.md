# Mail Filter

A lightweight, headless IMAP mail filtering engine designed to run continuously on a Raspberry Pi.

Mail Filter connects to one or more IMAP accounts, evaluates unseen messages against ordered configuration-driven rules, and moves, deletes, or marks messages according to those rules.

## Features

- Multiple IMAP accounts
- JSON5 configuration
- Ordered, first-match rule evaluation
- Logical folder mappings
- Recipient matching on the `To` header (full address or local part)
- Sender email-address and display-name matching
- MIME-decoded subject matching
- Exact, substring, prefix, and suffix matching for every field
- Invalid rules (unknown match fields, empty matches) rejected at startup
- Move, trash, delete, and mark-read actions
- Per-account on/off switch (`mail_enabled`)
- Optional catch-all routing
- Per-account dry run (`"dry_run": true`)
- Live IMAP tests against your own servers (`imap_tests.py`)
- Raspberry Pi / cron deployment

## Project Structure

```text
mail-filter/
├── mail_filter.py
├── list_folders.py
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
│   ├── architecture.md
│   ├── configuration.md
│   ├── rule-reference.md
│   ├── operations.md
│   ├── imap-notes.md
│   ├── development.md
│   └── decisions/
│       └── <project ADRs>
├── tests/
│   ├── test_mail_filter.py
│   ├── test_live_imap.py
│   ├── test_recorded_imap.py     # replays recorded server replies
│   ├── imap_recording.py
│   └── fixtures/imap/            # recordings (imap_tests.py --record)
└── README.md
```

## Documentation

### Architecture

[docs/architecture.md](docs/architecture.md)

System structure, processing flow, and architectural decisions.

### Configuration

[docs/configuration.md](docs/configuration.md)

Accounts, folders, rules, JSON5 configuration, and local vs example files.

### Rule Reference

[docs/rule-reference.md](docs/rule-reference.md)

The implemented rule language, match semantics, actions, ordering, and catch-all behavior.

### Operations

[docs/operations.md](docs/operations.md)

Running the filter, dry-run mode, folder inspection, cron, logging, and live-system safety.

### IMAP Notes

[docs/imap-notes.md](docs/imap-notes.md)

Current IMAP behavior, Gmail considerations, EXPUNGE, sequence numbers, and UID migration.

### Development

[docs/development.md](docs/development.md)

Development checkpoints, planned changes, the completed UID migration, the roadmap for Gmail handling, and [testing](docs/development.md#testing): offline unit tests and live IMAP tests against your own servers.

## Current Status

The current implementation is the known-good live baseline.

Message operations use IMAP UIDs rather than sequence numbers (see ADR 005). `move`, `trash`, and `delete` are distinct actions; on Gmail, `move` and `trash` use `UID MOVE` instead of `\Deleted` and EXPUNGE (see ADR 006).

IMAP behavior is checked by live tests against real servers and by recorded server replies replayed offline (see ADR 008 and [Testing](docs/development.md#testing)).

The project intentionally keeps those changes separate so the current live filtering system remains easy to revert.

## Safety

This software operates on real email.

Set `"dry_run": true` on an account when developing or modifying its rules, and create a Git checkpoint before making behavior-changing IMAP changes.

## License

Mail Filter is released under the [MIT License](LICENSE).

It includes [json5](https://github.com/dpranke/pyjson5) (Apache-2.0) in `vendor/json5/`; see [vendor/README.md](vendor/README.md).
