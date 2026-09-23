# ADR 005: Identify Messages by IMAP UID

**Status:** Accepted

## Context

The filter originally identified messages by IMAP sequence number. Sequence numbers are positions within the selected mailbox: when a message is expunged, every later message is renumbered. A sequence number obtained from a search is therefore not a stable identifier for the rest of a processing run.

Each message goes through several operations: fetch, optionally mark read, copy, and mark deleted. The current implementation expunges only after the processing loop, but planned Gmail move handling may need to expunge during iteration. That would shift the sequence numbers of messages not yet processed and could cause later operations to act on the wrong message.

## Decision

Identify messages by IMAP UID for all message-level operations, using `imaplib`'s `uid()` form:

```text
UID SEARCH UNSEEN
UID FETCH <uid> (BODY.PEEK[])
UID STORE <uid> +FLAGS (\Seen)
UID COPY <uid> "<target mailbox>"
UID STORE <uid> +FLAGS (\Deleted)
```

The UID returned by the search is carried through the entire processing flow for that message and is never converted back to a sequence number. Sequence-number and UID operations are not mixed.

Mailbox-level operations are unchanged: login, mailbox selection, logout, and the single plain `EXPUNGE` after the account's processing loop. The migration changes how messages are identified, not what the filter does with them.

## Consequences

- Message identity is stable within a processing run, independent of expunges or renumbering.
- Rule matching, actions, read/unread behavior, and move semantics are unchanged.
- Log lines now report message UIDs rather than sequence numbers.
- New message-level IMAP operations must use `imap.uid(...)`. Unlike `imap.store()`, `uid("STORE", ...)` does not add parentheses around flags, so flags are passed as `(\Seen)` / `(\Deleted)`.
- UIDs are only meaningful within a mailbox's UIDVALIDITY. The filter does not persist UIDs between runs, so no UIDVALIDITY check is needed; persisting UIDs in the future would require one.
- The Gmail move / EXPUNGE design can now be evaluated without sequence-number shifts as a constraint.

## Notes

The migration was verified through automated tests using a fake IMAP connection that fails on any sequence-number command, testing against development IMAP accounts on macOS, production testing on the Raspberry Pi, and normal cron execution on the production system. No behavioral differences were observed compared with the previous sequence-number implementation.

Gmail-specific folder/label behavior is addressed separately in [ADR 006](006-gmail-move-and-trash-semantics.md).
