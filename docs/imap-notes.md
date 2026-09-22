# IMAP Notes

## Current IMAP Model

The filter uses Python's `imaplib` over SSL.

Each account is connected independently.

The current processing sequence is:

```text
LOGIN
SELECT INBOX
SEARCH UNSEEN
FETCH
process
EXPUNGE
LOGOUT
```

## Message Fetching

Messages are fetched using:

```text
BODY.PEEK[]
```

This allows the message contents to be inspected without making the message read merely because it was fetched.

## Current Move Implementation

The current implementation performs a move as:

```text
COPY message → target mailbox
mark original \Deleted
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

The planned migration is to use UID commands for operations such as:

```text
UID SEARCH
UID FETCH
UID STORE
UID COPY
```

rather than sequence-number commands.

The UID migration should occur before introducing a processing model that expunges messages during iteration.

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
