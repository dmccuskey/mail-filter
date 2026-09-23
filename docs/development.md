# Mail Filter Development

## Development Philosophy

Mail Filter is a live system that operates on real email.

Development should therefore favor:

- small changes
- explicit checkpoints
- reversible commits
- behavior verification
- documentation of intentional design decisions

Avoid bundling multiple potentially disruptive IMAP changes into one commit.

## Current Baseline

The current implementation is the known-good live baseline.

It currently:

- supports multiple IMAP accounts
- uses JSON5 configuration
- uses logical folder mappings
- processes unseen messages
- evaluates ordered rules
- supports the `to_*`, `to_local_*`, `from_email_*`, `from_name_*`, and `subject_*` match fields, each with `_is`, `_contains`, `_starts_with`, and `_ends_with` (recipient fields read the `To` header only)
- rejects unknown match fields before connecting to any account
- supports `move`
- supports `trash` (to the optional `trash` mapping, otherwise the server-advertised `\Trash` mailbox)
- supports `delete`
- supports `mark_read`
- supports catch-all rules
- identifies messages by IMAP UID throughout processing
- uses Gmail label operations for `move` and `trash` on Gmail
- performs EXPUNGE after the processing loop when a message was marked `\Deleted`

This baseline should remain easy to restore.

## Planned Rule Features

The configuration/history contains several rule features that are not currently implemented:

- `not_subject_contains`
- `unread_only`
- `age_minutes_gt`
- `mark_unread`
- rule-level `log`

These should be implemented only when there is a concrete use case.

Before implementation, each feature should be defined in terms of:

1. configuration syntax
2. matching semantics
3. interaction with existing matchers
4. tests
5. documentation

## UID Migration

Message operations have been migrated from sequence numbers to UIDs (checkpoint 1).

The motivation is to make message identity robust when EXPUNGE occurs during processing.

The migration covers all message-specific operations consistently:

```text
UID SEARCH
UID FETCH
UID STORE
UID COPY
```

`EXPUNGE` is unchanged and still runs after the processing loop.

The migration has been verified by unit tests and on the deployed accounts, and is recorded in [ADR 005](decisions/005-uid-based-message-identification.md).

## Gmail Move and Trash Handling

Gmail move handling and the `trash` action have been implemented and verified on the deployed accounts, and are recorded in [ADR 006](decisions/006-gmail-move-and-trash-semantics.md).

The goal is to ensure that a message:

```text
is copied to its destination
AND
is removed from the source INBOX
```

with the expected Gmail label behavior.

On Gmail, `move` and `trash` copy the message (adding the destination label) and remove the `\Inbox` label with `X-GM-LABELS`, without relying on Gmail's configurable expunge behavior. Generic IMAP accounts keep COPY, `\Deleted`, and EXPUNGE.

Do not assume that behavior which is correct for Gmail is harmless on every other IMAP server.

## Testing Strategy

Unit tests use only the standard library and a fake IMAP connection (no network or credentials):

```bash
python3 -m unittest discover -s tests -v
```

For changes affecting message identity or deletion:

1. Start from the known-good baseline.
2. Commit before modifying behavior.
3. Test with `DRY_RUN` where applicable.
4. Test non-destructive operations first.
5. Verify behavior on the existing non-Gmail account(s).
6. Verify Gmail independently: destination label added, `\Inbox` removed, other labels kept, no `\Deleted` flag, and Trash behavior for `trash`.
7. Commit after successful verification.

## Development Checkpoints

Recommended sequence:

```text
CHECKPOINT 0
Known-good current implementation
        │
        ▼
CHECKPOINT 1
UID migration (complete; ADR 005)
        │
        ▼
CHECKPOINT 2
Gmail move / Trash behavior (complete; ADR 006)
        │
        ▼
CHECKPOINT 3
Additional rule features
```

Each checkpoint should leave the system in a known state.

## Historical Context

The project began as an effort to replace GUI mail filtering running on a Mac mini with a lower-power Raspberry Pi implementation.

The rule model evolved from simple address-to-folder routing into a configuration-driven rules engine with:

```text
name
match
do
```

The logical folder mapping was introduced so rules would not need to contain physical IMAP mailbox names.

Prefix matching and ordered rules provide a fall-through mechanism, allowing specific rules to precede broader routing rules such as:

```text
my-
shop-
dev-
body-
pol-
```

The current implementation is the result of that evolution and should be treated as the baseline rather than the historical prototypes.
