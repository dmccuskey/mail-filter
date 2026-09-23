# IMAP Notes

## Current IMAP Model

The filter uses Python's `imaplib` over SSL.

Each account is connected independently.

The current processing sequence is:

```text
LOGIN
SELECT INBOX
UID SEARCH UNSEEN
UID FETCH
process (UID STORE / UID COPY; Gmail label updates where supported)
EXPUNGE (only if a message was marked \Deleted)
LOGOUT
```

Messages are identified by IMAP UID throughout processing (see [UID Migration](#uid-migration)).

## Message Fetching

Messages are fetched using:

```text
BODY.PEEK[]
```

This allows the message contents to be inspected without making the message read merely because it was fetched.

## Current Move Implementation

The current implementation performs a move as:

```text
UID COPY message → target mailbox
UID STORE original +FLAGS (\Deleted)  [generic IMAP]
```

After all messages for the account have been processed:

```text
EXPUNGE
```

is called.

This is the known-good baseline for non-Gmail servers, and it is also how `trash` is performed there.

For Gmail, the move is:

```text
UID COPY message → target mailbox   (adds the label)
UID STORE original -X-GM-LABELS (\Inbox)
```

This avoids marking the message `\Deleted` and avoids `EXPUNGE`. Other Gmail labels remain attached.

## Gmail

Gmail's IMAP model differs from traditional mailbox-oriented IMAP because Gmail represents mail organization primarily through labels. The filter detects Gmail by the `X-GM-EXT-1` capability.

This means that:

```text
COPY → target
\Deleted → source
```

does not necessarily have exactly the same visible semantics as a traditional IMAP mailbox move.

Gmail interprets `\Deleted` and `EXPUNGE` through two account settings:

- Auto-Expunge: when on, `\Deleted` expunges the message from the selected folder immediately, during the processing loop.
- The disposition of a message expunged from its last visible folder: archive (default), move to Trash, or delete forever.

The `trash` action copies the message to the Trash mailbox (for example `[Gmail]/Trash`) and removes `\Inbox`. Gmail purges Trash after 30 days.

The `delete` action keeps the generic `\Deleted` plus `EXPUNGE` semantics, so on Gmail it usually archives the message.

If the `CAPABILITY` query fails, the provider is unknown and `move` and `trash` are skipped rather than processed with generic semantics.

## Trash Mailbox Discovery

Without a `trash` mapping, the Trash mailbox comes from the RFC 6154 `\Trash` attribute in a plain `LIST`. `imaplib`'s `list()` returns each line without the `* LIST` prefix, for example:

```text
(\HasNoChildren \Trash) "/" "[Gmail]/Trash"
(\HasNoChildren \UnMarked \Trash) "." INBOX.Trash
```

A name sent as an IMAP literal arrives as a tuple of the line and the name, and an empty result arrives as `[None]`. The advertised name is reused exactly. Exactly one `\Trash` mailbox must be advertised; otherwise Trash is unresolved and `trash` is skipped.

## EXPUNGE and Sequence Numbers

A significant issue was identified during investigation of immediate Gmail expunging.

IMAP message sequence numbers are not stable across an EXPUNGE.

If a program:

1. searches for a batch of messages,
2. stores their sequence numbers,
3. expunges one message,
4. continues using the original sequence numbers,

the remaining sequence numbers can shift.

Therefore, introducing an EXPUNGE inside the message-processing loop can change the meaning of sequence numbers that were obtained earlier in the same batch.

## UID Migration

IMAP UIDs provide a stable message identifier within the mailbox's UID validity scope.

The UID migration has been implemented. All message-level operations use `imaplib`'s `uid()` form:

```text
UID SEARCH UNSEEN
UID FETCH <uid> (BODY.PEEK[])
UID STORE <uid> +FLAGS (\Seen)
UID STORE <uid> +FLAGS (\Deleted)
UID COPY <uid> "<target mailbox>"
```

The UID returned by the search is used for every subsequent operation on that message; it is never converted back to a sequence number.

`EXPUNGE` remains a plain, mailbox-level command and runs once after the processing loop, only when a message was marked `\Deleted`. Gmail `move` and `trash` do not require it.

The UID migration is a prerequisite for any processing model that expunges messages during iteration. It has been verified by unit tests and on the deployed accounts, and is recorded in [ADR 005](decisions/005-uid-based-message-identification.md).

## Important Baseline Constraint

The current system is live.

Therefore the following were kept as separate checkpoints:

```text
current implementation
        ↓
UID migration
        ↓
verification
        ↓
Gmail move and Trash behavior (ADR 006)
        ↓
verification
```

The UID migration and the Gmail move/Trash change were implemented and verified as separate checkpoints.

## Gmail Move and Trash Decision

The Gmail move and Trash behavior is recorded in [ADR 006](decisions/006-gmail-move-and-trash-semantics.md) and has been verified on both production accounts.
