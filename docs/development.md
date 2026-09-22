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
- supports `to`
- supports `to_prefix`
- supports `from_contains`
- supports `from_name_contains`
- supports `subject_contains`
- supports `subject_equals`
- supports `move`
- supports `delete`
- supports `mark_read`
- supports catch-all rules
- identifies messages by IMAP UID throughout processing
- performs EXPUNGE after the processing loop

This baseline should remain easy to restore.

## Planned Rule Features

The configuration/history contains several rule features that are not currently implemented:

- `from` exact-address matching
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

The migration is unit-tested; live verification on the deployed accounts should be completed and committed independently before changing Gmail move behavior.

## Gmail Move Handling

Gmail move semantics are the next major IMAP behavior change.

The goal is to ensure that a message:

```text
is copied to its destination
AND
is removed from the source INBOX
```

with the expected Gmail label behavior.

This may require Gmail-specific handling.

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
6. Verify Gmail independently.
7. Commit after successful verification.

## Development Checkpoints

Recommended sequence:

```text
CHECKPOINT 0
Known-good current implementation
        │
        ▼
CHECKPOINT 1
UID migration (implemented; live verification pending)
        │
        ▼
CHECKPOINT 2
Gmail move / EXPUNGE behavior
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
