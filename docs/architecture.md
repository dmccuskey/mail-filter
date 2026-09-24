# Mail Filter Architecture

## Overview

Mail Filter is a lightweight, headless IMAP mail filtering engine designed to run continuously on a Raspberry Pi.

It replaces GUI-based mail filtering with a deterministic, configuration-driven Python program.

The system:

1. Connects to one or more IMAP accounts.
2. Selects the account's `INBOX`.
3. Finds unseen messages.
4. Fetches each message without changing its read state.
5. Extracts recipients, sender information, and subject.
6. Evaluates the configured rules in order.
7. Applies the first matching rule.
8. Removes moved or trashed messages from `INBOX`: by removing the `\Inbox` label on Gmail, or by marking them `\Deleted` on other servers.
9. Expunges messages marked `\Deleted`, once, after the processing loop.
10. Logs the result.

## System Structure

```text
                    accounts.local.json5
                             │
                             ▼
                     ┌───────────────┐
                     │   Accounts    │
                     └───────┬───────┘
                             │
                 ┌───────────┴───────────┐
                 ▼                       ▼
        folders.local.json5       rules.local.json5
                 │                       │
                 ▼                       ▼
        logical → IMAP mailbox     match → action
                 │                       │
                 └───────────┬───────────┘
                             ▼
                       mail_filter.py
                             │
                             ▼
                          IMAP
```

## Configuration-Driven Design

The implementation separates three concerns:

### Accounts

`accounts.local.json5` contains the connection information required to access each IMAP account.

### Folders

`folders.local.json5` maps stable, logical folder keys to the actual mailbox names used by each IMAP server.

Rules therefore don't need to know whether a mailbox is named:

```text
INBOX.Services.Payments
```

or:

```text
Services/Payments
```

They refer to a logical key such as:

```text
services_payments
```

### Rules

`rules.local.json5` defines how messages are classified and what action should be taken.

This keeps matching logic separate from the physical mailbox structure.

Before any account is processed, every account's rules are checked. If any rule uses an unknown match field, the filter logs every one (account, rule number and name, field), processes no mail, and exits with status 1 before any connection is made. A misspelled field can therefore never silently drop a condition (see [Rule Reference](rule-reference.md#unknown-match-fields)).

## Account Processing

Each configured account is processed independently.

For an account, the engine:

```text
connect
  ↓
login
  ↓
validate configured folders
  ↓
select INBOX
  ↓
UID SEARCH UNSEEN
  ↓
process messages
  ↓
EXPUNGE
  ↓
logout
```

A missing folder configuration or missing rule configuration causes that account to be skipped rather than processed with incomplete configuration.

## Message Processing

For each unseen message:

```text
IMAP message
     │
     ├── recipients
     │      └── To header only (not Cc / Delivered-To / X-Original-To)
     │
     ├── subject
     │      └── MIME decoded
     │
     └── sender
            ├── display name
            └── email address
                    │
                    ▼
                choose_rule()
                    │
                    ▼
             first matching rule
                    │
        ┌─────────┬─┴───────┬─────────┐
        ▼         ▼         ▼         ▼
      move      trash     delete    no-op
```

## Rule Evaluation

Rules are evaluated from top to bottom.

The first matching rule wins.

Within a rule:

- Different match fields are combined with AND semantics.
- Multiple values within a match field use OR semantics.
- String matching is case-insensitive.
- Subject headers are MIME-decoded before matching.

## Actions

The currently implemented actions are:

- `move`
- `trash`
- `delete`
- `mark_read`

If more than one of `move`, `trash`, and `delete` is specified, the least destructive wins: `move`, then `trash`, then `delete`.

`trash` sends the message to the account's Trash mailbox: the optional `trash` mapping, otherwise the mailbox the server advertises with `\Trash`. On Gmail (detected by the `X-GM-EXT-1` capability), `move` and `trash` copy the message and remove its `\Inbox` label instead of using `\Deleted` and `EXPUNGE`. `delete` keeps the `\Deleted` plus `EXPUNGE` behavior on every provider. See [ADR 006](decisions/006-gmail-move-and-trash-semantics.md).

If a rule matches with only `mark_read`, the message is marked read and stays in `INBOX`. If it has no action at all, the message is left unchanged.

## Safety Model

The system is intentionally deterministic.

There is no machine-learning classification or probabilistic decision-making in the current implementation.

Rules explicitly define:

```text
WHEN message matches these conditions
THEN perform this action
```

`DRY_RUN` is available to test behavior without performing destructive actions.

## Design History

The project evolved from GUI mail filtering toward a small, headless rules engine.

The major architectural steps were:

1. Move filtering from a Mac mini to a Raspberry Pi.
2. Use Python and IMAP instead of GUI mail rules.
3. Support multiple accounts.
4. Separate logical folder names from server mailbox names.
5. Introduce an explicit `name` / `match` / `do` rule structure.
6. Add prefix-based routing and ordered fall-through behavior.
7. Add sender name and decoded-subject matching.
8. Handle Gmail's label-based move behavior explicitly and add a separate `trash` action.

The current implementation is the baseline for future changes.

## Architecture Decisions

The durable records for the project's accepted architectural decisions are in [decisions/](decisions/):

- [ADR 001: Headless Raspberry Pi / Python IMAP Architecture](decisions/001-headless-pi-python-imap.md)
- [ADR 002: Configuration-Driven Filtering and Configuration Organization](decisions/002-configuration-driven-filtering.md)
- [ADR 003: Logical Folder Abstraction](decisions/003-logical-folder-abstraction.md)
- [ADR 004: Ordered First-Match Rule Evaluation](decisions/004-first-match-rule-evaluation.md)
- [ADR 005: Identify Messages by IMAP UID](decisions/005-uid-based-message-identification.md)
- [ADR 006: Gmail Move Semantics and an Explicit Trash Action](decisions/006-gmail-move-and-trash-semantics.md)
- [ADR 007: Account-Keyed Configuration Files](decisions/007-account-keyed-configuration.md)
