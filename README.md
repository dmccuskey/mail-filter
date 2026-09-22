# Mail Filter

A lightweight, headless IMAP mail filtering engine designed to run continuously on a Raspberry Pi.

Mail Filter connects to one or more IMAP accounts, evaluates unseen messages against ordered configuration-driven rules, and moves, deletes, or marks messages according to those rules.

## Features

- Multiple IMAP accounts
- JSON5 configuration
- Ordered, first-match rule evaluation
- Logical folder mappings
- Recipient prefix routing
- Sender and sender-name matching
- MIME-decoded subject matching
- Move, delete, and mark-read actions
- Optional catch-all routing
- Dry-run support
- Raspberry Pi / cron deployment

## Project Structure

```text
mail-filter/
├── mail_filter.py
├── list_folders.py
├── accounts.example.json5
├── accounts.local.json5
├── folders.example.json5
├── folders.local.json5
├── rules.example.json5
├── rules.local.json5
├── docs/
│   ├── architecture.md
│   ├── configuration.md
│   ├── rule-reference.md
│   ├── operations.md
│   ├── imap-notes.md
│   └── development.md
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

Development checkpoints, planned changes, and the roadmap for UID migration and Gmail handling.

## Current Status

The current implementation is the known-good live baseline.

The next planned infrastructure change is migration from IMAP sequence numbers to UIDs. Gmail-specific move/EXPUNGE behavior will be addressed separately after that change has been verified.

The project intentionally keeps those changes separate so the current live filtering system remains easy to revert.

## Safety

This software operates on real email.

Use `DRY_RUN = True` when developing or modifying rules, and create a Git checkpoint before making behavior-changing IMAP changes.