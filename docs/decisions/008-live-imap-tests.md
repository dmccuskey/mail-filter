# ADR 008: Live IMAP Tests

**Status:** Accepted

## Context

The unit tests check the exact IMAP commands the filter sends, against a fake connection. They cannot show what a real server does with those commands. Until now that was checked by hand for each change, by sending mail, running the filter, and inspecting the mailboxes, which is slow and easy to get wrong. Other people running the filter have no way to check their own servers.

The live tests have to write to real accounts, some of them personal, so the main risk is harming real mail.

## Decision

Add live tests (`tests/test_live_imap.py`, run with `imap_tests.py`) that create their own messages with IMAP `APPEND`, run the real `process_account` on them, check where they ended up, and delete what they created.

- **Opt-in to run, opt-out per account.** The tests run only through `imap_tests.py`; `unittest discover` skips them. They test every account in `accounts.local.json5` unless it sets `"test_enabled": false`.
- **Marked test messages.** Each message has a subject starting `[mail-filter testing only]` with the run ID, an `X-Mail-Filter-Test` header holding the run ID, and a `Message-ID` in the reserved `mail-filter.invalid` domain.
- **Verify before touching.** A message is changed or deleted only after its `X-Mail-Filter-Test` header has been fetched and matches. A subject match alone is never enough.
- **The filter runs on test messages only.** A test run searches for its own run ID and processes only messages whose header matches it. A normal run logs any message with the header as `IGNORED` and leaves it alone, so cron cannot move test mail with the real rules. The one test of that normal-run path narrows its search to the test's own messages.
- **Precise deletes.** Test messages are deleted with `UID EXPUNGE`, so no other `\Deleted` message is expunged. Without UIDPLUS, a plain `EXPUNGE` is used only in a folder the run created; elsewhere the message is left `\Deleted` and a warning is printed. On Gmail, messages are moved to Trash and deleted there, which is Gmail's permanent delete.
- **Own folders.** The tests use `mail-filter-live-tests/...` folders, a name that belongs only to the harness. At the end of a run, each of these folders is deleted if it is empty, whichever run created it. A folder that still holds messages is kept, and so is the parent while a subfolder remains. Other folders are never deleted.
- **Recorded replies.** `imap_tests.py --record` saves the filter's own IMAP conversations, scrubbed of the username, host, account ID, and personal mailbox names, to `tests/fixtures/imap/`. The unit tests replay them offline and require the log of the live run, so real server replies keep being checked without a server.
- **Crash recovery.** Each run first sweeps test messages from runs that started more than an hour earlier; the hour protects a run still in progress elsewhere. `--keep` skips cleanup for inspection. Its messages are swept by a later run, and its folders are then deleted because they are empty.

## Consequences

- IMAP behavior can be checked on every supported server with one command, and anyone can check their own server.
- Test messages appear briefly in the tested accounts.
- The filter has a small test-only path: the `test_run_id` parameter and the `IGNORED` result. A real message with an `X-Mail-Filter-Test` header would be ignored, which is not a realistic case.
- The harness found three bugs in normal filtering, all since fixed:
  - On Gmail, `move` and `trash` to a normal label never removed `\Inbox`. The command Gmail accepts and ignores was replaced with `UID MOVE` ([ADR 006 amendment](006-gmail-move-and-trash-semantics.md#amendment-gmail-uses-uid-move)).
  - Folded headers were not unfolded. That broke subject matching across a fold, and split log lines in two (`decode_mime_header`).
  - The filter crashed when a server added an unsolicited flag update to a FETCH reply ahead of the message body (`fetched_body`).
