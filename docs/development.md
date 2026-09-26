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

- supports multiple IMAP accounts, each of which can be turned off with `mail_enabled`
- uses JSON5 configuration
- uses logical folder mappings
- processes unseen messages
- unfolds folded headers before matching and logging
- leaves messages created by the live IMAP tests alone (logged as `IGNORED`)
- evaluates ordered rules
- supports the `to_*`, `to_local_*`, `from_email_*`, `from_name_*`, and `subject_*` match fields, each with `_is`, `_contains`, `_starts_with`, and `_ends_with` (recipient fields read the `To` header only)
- rejects invalid rules (unknown match fields, empty matches) before connecting to any account
- supports `move`
- supports `trash` (to the optional `trash` mapping, otherwise the server-advertised `\Trash` mailbox)
- supports `delete`
- supports `mark_read`
- supports catch-all rules
- identifies messages by IMAP UID throughout processing
- uses `UID MOVE` for `move` and `trash` on Gmail ([ADR 006 amendment](decisions/006-gmail-move-and-trash-semantics.md#amendment-gmail-uses-uid-move))
- on other servers, copies moved messages, marks the originals `\Deleted`, and performs EXPUNGE after the processing loop
- is tested by unit tests, live IMAP tests against real servers, and recorded server replies replayed offline (see [Testing](#testing))

This baseline should remain easy to restore.

## Possible Future Changes

Decided work is tracked as [GitHub issues](https://github.com/dmccuskey/mail-filter/issues). The ideas below have come up but are not decided. Each needs further discussion before it is worked on, and should be implemented only when there is a concrete use case.

### Rule Features

The configuration/history contains several rule features that are not currently implemented:

- `not_subject_contains`
- `unread_only`
- `age_minutes_gt`
- `mark_unread`
- rule-level `log`

Possible new match features:

- `cc_*` matchers for the `Cc` header
- configurable AND/OR combination of match values

Before implementation, each feature should be defined in terms of:

1. configuration syntax
2. matching semantics
3. interaction with existing matchers
4. tests
5. documentation

### Testing

Left open when the live IMAP tests were added ([issue #1](https://github.com/dmccuskey/mail-filter/issues/1)):

- offline rule regression tests: check that the real `rules.local.json5` routes known messages to the expected rule, with one command and no server. Most useful for large or often-edited rule sets; with a small set, spotting a misrouted message in the mail client, moving it back to `INBOX`, and adjusting the rule with `"dry_run": true` works well.
  - examples as a gitignored case file of `{from, to, subject}` and the expected rule (or none), run through `choose_rule()`;
  - or, closer to real mail, a gitignored folder of `.eml` files (for example dragged out of Apple Mail), which also tests header parsing on what senders actually send: `To` headers without your address, MIME-encoded subjects and names, folded headers;
  - the `.eml` version needs header parsing (recipients, decoded subject, sender name and address) moved out of `process_account()` into a helper.
- Gmail: a live check that a moved message keeps its other labels ([issue #2](https://github.com/dmccuskey/mail-filter/issues/2))
- Dovecot in Docker: run the generic IMAP live tests against a throwaway server, without an account, and in GitHub Actions on every push. A default Dovecot adds little over a real Dovecot account; the value is in server setups that real accounts rarely offer and nothing tests live today: no `UIDPLUS`, no `\Trash` special-use mailbox, `/` as the delimiter without an `INBOX.` prefix, no `MOVE`. Worth building when the filter is used on such servers (for example, after a bug report from one). Needs an `imap_port` setting and handling of the container's self-signed certificate, since the filter always connects with `IMAP4_SSL` on port 993.

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

On Gmail, `move` and `trash` use `UID MOVE`, which adds the destination label and removes `\Inbox` in one step, without relying on Gmail's configurable expunge behavior. (Removing `\Inbox` with `X-GM-LABELS` while `INBOX` is selected looks successful but does nothing; see the ADR 006 amendment.) Generic IMAP accounts keep COPY, `\Deleted`, and EXPUNGE.

Do not assume that behavior which is correct for Gmail is harmless on every other IMAP server.

## Testing

Testing has two halves:

- **Unit tests** (`tests/test_mail_filter.py`) check rule matching, configuration checks, and the exact IMAP commands sent, using a fake IMAP connection. They use only the standard library and need no network or credentials.
- **Live IMAP tests** (`tests/test_live_imap.py`, run with `imap_tests.py`) run the real filter against real servers, to check that each server does what those commands are meant to do.

Between the two, **recorded IMAP replies** (`tests/test_recorded_imap.py`) replay conversations saved from real servers during a live run, offline, as part of the unit tests.

### Unit Tests

```bash
python3 -m unittest discover -s tests -v
```

Run them after every change. They report the live tests as skipped; `unittest discover` never contacts a server.

### Live IMAP Tests

```bash
python3 imap_tests.py                    # every account with test_enabled
python3 imap_tests.py gmail-main         # only the accounts named
python3 imap_tests.py --keep gmail-main  # leave the results on the server to inspect
```

The tests use the accounts in `accounts.local.json5`. Every account is tested unless it sets `"test_enabled": false` (see [`test_enabled`](configuration.md#test_enabled)); `mail_enabled` does not matter, so an account kept only for testing can set `"mail_enabled": false`. The rules and folders files are not used: the tests bring their own rules. Source the secrets file first if accounts use `password_env`.

**Gmail setup.** In Gmail, open Settings → Labels and check "Show in IMAP" for **All Mail** and **Trash**:

- Without All Mail, the tests cannot check a moved message's labels, and cleanup cannot find messages that have left `INBOX` (on Gmail, a deleted message usually only loses its `\Inbox` label and stays in All Mail). Those messages are left behind with a warning.
- Without Trash, the Trash test is skipped. Cleanup cannot delete anything either, because Gmail deletes permanently only from Trash.

The Settings → Forwarding and POP/IMAP choice for deleted messages (archive, move to Trash, or delete forever) does not matter. The delete test only checks that the message left `INBOX`, and cleanup looks in both All Mail and Trash.

For each account, the tests:

1. create the folders `mail-filter-live-tests/moved` and `mail-filter-live-tests/alt-trash` (with the server's own delimiter and prefix, for example `INBOX.mail-filter-live-tests.moved`), if they do not already exist;
2. add unread test messages to `INBOX` with IMAP `APPEND`, one per case, each with its own sender, name, recipient, and subject;
3. run the filter on just those messages and check each message's log line, where it ended up, its `\Seen` flag, that it is not marked `\Deleted`, and, on Gmail, its labels: move, move with `mark_read`, trash to the server's `\Trash` and to a `trash` mapping, delete, `mark_read` alone, a rule with no action, no matching rule, a missing folder mapping, a missing mailbox, MIME-encoded sender names, a dry run, and a normal run that must ignore test messages;
4. permanently delete the test messages (on Gmail, through Trash), then delete the `mail-filter-live-tests` folders if they are empty.

Test messages have a subject starting `[mail-filter testing only]`, an `X-Mail-Filter-Test` header holding the run's ID, and a `Message-ID` ending `@mail-filter.invalid`. The tests change or delete a message only after reading that header, and delete only their own empty `mail-filter-live-tests` folders, so real mail and your other folders are never touched. A normal filter run logs test messages as `IGNORED` and leaves them alone, so a cron run cannot interfere with a test in progress. See [ADR 008](decisions/008-live-imap-tests.md).

Test messages appear briefly in the tested accounts; a mail client may notify you of them.

After the tests, each account prints either `Cleaning up test run <id>: ...` followed by `Cleanup done: deleted <n> test message(s); deleted folders: ...`, or, with `--keep`, `Keeping test run <id>: ...` with the test folders left in place. A run that removes leftovers from earlier runs also prints `Removed <n> test message(s) left by earlier runs`. Anything the tests could not clean up is printed as a `Warning:` line. A failed test shows its own filter log line and, when a message is in the wrong place, its flags and Gmail labels; the filter's full log for the run is in `imap_tests.log` (gitignored).

`--keep` skips the cleanup, so the messages and folders can be inspected in a mail client. Afterward:

- **Messages** are removed by the first run that starts more than an hour after the kept run did. Each run begins by sweeping test messages from runs that started more than an hour earlier. The hour protects a run that is still in progress on another machine. The same sweep removes messages a crashed run left behind. To remove them sooner, delete them by hand.
- **Folders** are deleted at the end of the first later run that finds them empty, which is normally the same run that sweeps the messages. Until then, a run leaves the folders in place and prints a `kept folder ... not empty` warning.

If a server fails the live tests, set `"test_enabled": false` for that account and open a GitHub issue with the `imap_tests.py` output (remove any addresses you do not want to share).

### Recorded IMAP Replies

A hand-written fake server returns exactly the replies the code expects, so it cannot catch a wrong assumption about what real servers send. Recordings catch those. For example, a server may add an unsolicited flag update to a FETCH reply, or Gmail may answer `UID MOVE` with an empty reply.

```bash
python3 imap_tests.py --record
```

This runs the live tests as usual and also saves the filter's own IMAP conversation for each account. Each command it sent is saved with the reply `imaplib` returned. The file is `tests/fixtures/imap/gmail.txt` for Gmail and `tests/fixtures/imap/imap.txt` for other servers; a second account of the same kind gets `imap-2.txt`, and so on. An existing recording is overwritten.

`python3 -m unittest discover tests` replays every recording. It runs `process_account` against the recorded replies and requires exactly the log lines the live run produced. Replies are looked up by the exact command, not by position, so reordering commands does not break a recording. A recording goes stale in two ways:

- the filter sends a new or changed IMAP command, which fails with `IMAP command not in the recording`;
- the filter's log output changes, which fails because the replayed log no longer matches the recorded one.

When the change is intended, run `python3 imap_tests.py --record` on the branch and commit the new recordings together with the change.

Recordings are committed to the public repository, so they are scrubbed as they are written:

- the password is never recorded (login is not recorded);
- the account's username and IMAP host become `user@example.com` and `imap.example.com`, and the account ID becomes `test`;
- in `LIST` replies, every mailbox except `INBOX`, special-use mailboxes (`\Trash`, `\All`, `\Sent`, `\Junk`, `\Drafts`, ...), and the test folders is renamed `Folder-1`, `Folder-2`, and so on.

The filter fetches only the test messages, so no real message content is recorded.

#### Reviewing a Recording

Recordings are public once committed. Before committing a new or changed recording, check it:

```bash
python3 imap_tests.py --check-recordings
```

`--record` runs the same check at the end. It reads `accounts.local.json5` and reports an `ERROR` for each of these found in `tests/fixtures/imap/*.txt`:

- any account's username, the parts of it before and after the `@`, IMAP host, or account ID (whole words only, ignoring case; `gmail.com` is allowed);
- any email address outside `example.com`, `example.org`, `example.net`, and `mail-filter.invalid` (the placeholders and test messages);
- any `LIST` mailbox name other than `INBOX`, special-use mailboxes, the test folders, and `Folder-N` placeholders.

Do not commit a recording the check rejects. Fix the cause, usually in the scrubbing in `tests/imap_recording.py`, and record again.

The check cannot know everything that identifies you, for example a name that is not part of any configured account. So also skim the diff, and look at anything new in the logs and in `FETCH` and `LIST` replies. A recording made on a dedicated test account holds the least personal data.

### Behavior Changes

For changes affecting message identity or deletion:

1. Start from the known-good baseline.
2. Commit before modifying behavior.
3. Run the unit tests.
4. Run `python3 imap_tests.py --record` against a Gmail account and a non-Gmail account. Review the updated recordings ([Reviewing a Recording](#reviewing-a-recording)) and commit them.
5. Test with `"dry_run": true` on the real rules where applicable.
6. Commit after successful verification.

## Branches

`main` holds only code that has passed testing, because the Raspberry Pi deployment pulls `main` and runs it against real mail.

Each change is developed on its own short-lived branch, named for the change:

```text
feat/<name>    new behavior
fix/<name>     bug fixes
docs/<name>    documentation only
```

Workflow:

1. Branch from an up-to-date `main`: `git switch main && git pull && git switch -c fix/<name>`.
2. Commit on the branch as often as useful; small commits are still preferred (see [Development Philosophy](#development-philosophy)).
3. Before merging, run the unit tests. If the change touches IMAP behavior or the log output, also run `python3 imap_tests.py --record` on a Gmail account and a non-Gmail account, then review the updated recordings ([Reviewing a Recording](#reviewing-a-recording)) and commit them. Documentation-only changes need neither.
4. Merge into `main` with a GitHub pull request or `git merge`, push, and delete the branch.
5. Update the deployment: `git pull` in `/home/pi/mail-filter`.

A branch holds one change. Unrelated changes go on separate branches, so each can be tested, merged, and reverted on its own.

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
Live IMAP tests and recorded replies (complete; ADR 008)
        │
        ▼
CHECKPOINT 4
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
