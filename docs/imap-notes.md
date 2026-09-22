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
process (UID STORE / UID COPY)
EXPUNGE
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
UID STORE original +FLAGS (\Deleted)
```

After all messages for the account have been processed:

```text
EXPUNGE
```

is called.

This is the current known-good baseline.

## Gmail

Gmail's IMAP model differs from traditional mailbox-oriented IMAP because Gmail represents mail organization primarily through labels.

This means that:

```text
COPY → target
\Deleted → source
```

does not necessarily have exactly the same visible semantics as a traditional IMAP mailbox move.

The project has therefore identified Gmail move behavior as an area requiring explicit testing and potentially provider-specific handling.

The Gmail-specific change has not yet been incorporated into the current implementation.

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

`EXPUNGE` remains the plain, mailbox-level command and still runs once after the processing loop. The migration changed message identity only, not processing behavior.

The UID migration is a prerequisite for any processing model that expunges messages during iteration. It has been unit-tested against a fake IMAP connection; live verification on the deployed accounts is still pending.

## Important Baseline Constraint

The current system is live.

Therefore the following should remain separate checkpoints:

```text
current implementation
        ↓
UID migration
        ↓
verification
        ↓
Gmail-specific move behavior
        ↓
verification
```

Do not combine the UID migration and Gmail EXPUNGE behavior into one unverified change.

## Open Gmail Question

The exact final architecture for Gmail move handling remains open.

Possible approaches include:

- keeping the move implementation generic where standard IMAP behavior is sufficient
- adding provider-specific handling for Gmail
- using UID-based operations throughout and then implementing Gmail-specific source removal

The choice should be made after the UID migration is complete and tested.
