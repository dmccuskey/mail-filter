# ADR 001: Headless Raspberry Pi / Python IMAP Architecture

**Status:** Accepted

## Context

Mail filtering previously depended on rules in a GUI mail client running on a Mac mini. The filter needs to operate unattended, remain available without a desktop session, and work across one or more IMAP accounts.

## Decision

Run Mail Filter as a lightweight, headless Python program on an always-on Raspberry Pi. Connect directly to each configured account using IMAP over SSL through Python's `imaplib`.

The program is configuration-driven: it connects to an account, selects its `INBOX`, processes unseen messages, applies configured rules, expunges pending deletions after the account's processing loop, and logs the results.

## Consequences

- Filtering is independent of a GUI mail client and a Mac desktop session.
- The system can run unattended on low-power hardware, including from cron.
- IMAP provides a common protocol-level interface for the configured providers.
- Provider-specific IMAP behavior remains possible and must be handled deliberately when it affects correctness.

## Notes

The present message-identity model uses IMAP sequence numbers. Migrating message-specific operations to IMAP UIDs is a **proposed** future change, not part of this accepted decision.

Gmail's label-oriented move behavior is an **open** design question. It will be evaluated separately after the UID migration has been implemented and verified.
