"""
Live IMAP tests: run the real filter against real IMAP servers.

These tests create messages in each test-enabled account's INBOX (IMAP APPEND),
run process_account on them, check where the messages ended up, and remove
everything they created. Run them with imap_tests.py, not unittest discover;
see docs/development.md#testing.

Every test message is marked three ways (see ADR 008):
  Subject:            [mail-filter testing only] <run id> <batch id> <case> ...
  X-Mail-Filter-Test: <batch id>       (the batch id starts with the run id)
  Message-ID:         <<batch id>.<case>@mail-filter.invalid>
Nothing is modified or deleted until its X-Mail-Filter-Test header is verified.
"""
import email
import imaplib
import io
import os
import re
import secrets
import sys
import time
import unittest
from collections import namedtuple
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from email.headerregistry import Address
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formatdate
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import mail_filter  # noqa: E402
import imap_recording  # noqa: E402

LIVE = os.environ.get("MAILFILTER_LIVE_TESTS") == "1"
KEEP = os.environ.get("MAILFILTER_LIVE_KEEP") == "1"
RECORD = os.environ.get("MAILFILTER_LIVE_RECORD") == "1"
ACCOUNTS_ENV = "MAILFILTER_LIVE_ACCOUNTS"  # comma-separated IDs; empty means all

BASE = Path(mail_filter.__file__).resolve().parent
TEST_ROOT = "mail-filter-live-tests"
MARKER = mail_filter.TEST_SUBJECT_MARKER
SWEEP_QUERY = "mail-filter testing only"  # the marker's words; brackets upset Gmail search
WAIT_SECONDS = 15  # Gmail's search can lag behind APPEND and moves
STALE_AFTER = timedelta(hours=1)  # older runs' leftovers are swept at startup
LOG_FILE = BASE / "imap_tests.log"  # the filter's full output; imap_tests.py empties it
FIXTURES = BASE / "tests" / "fixtures" / "imap"  # recordings; see imap_recording.py
RECORDED_THIS_RUN = set()  # recording names already written by this run

Found = namedtuple("Found", "uid test_id flags labels")


def new_run_id():
    """Letters and digits only, so every server's SUBJECT search sees one word."""
    return "mft" + datetime.now().strftime("%Y%m%d%H%M%S") + secrets.token_hex(3)


def is_stale(test_id, now=None):
    """True for a test ID from a run that started more than STALE_AFTER ago."""
    match = re.match(r"mft(\d{14})", test_id or "")
    if not match:
        return False
    started = datetime.strptime(match.group(1), "%Y%m%d%H%M%S")
    return (now or datetime.now()) - started > STALE_AFTER


def load_live_accounts(environ):
    """
    Return ({account_id: cfg}, problems) for the accounts to test: those
    named in MAILFILTER_LIVE_ACCOUNTS, else every account with test_enabled.
    """
    try:
        accounts_all = mail_filter.load_json(BASE / "accounts.local.json5")
    except (OSError, ValueError) as e:
        return {}, [f"cannot read accounts.local.json5: {e}"]
    problems = mail_filter.validate_config_format(accounts_all, {})
    if problems:
        return {}, problems

    names = [name for name in environ.get(ACCOUNTS_ENV, "").split(",") if name]
    for name in names:
        if name not in accounts_all:
            problems.append(f"account '{name}' is not in accounts.local.json5")
        elif not mail_filter.test_enabled(accounts_all[name]):
            problems.append(f"account '{name}' has \"test_enabled\": false")
    if problems:
        return {}, problems

    selected = {
        account_id: cfg for account_id, cfg in accounts_all.items()
        if (account_id in names if names else mail_filter.test_enabled(cfg))
    }
    if not selected:
        return {}, ["no account has test_enabled; nothing to test"]
    return selected, mail_filter.validate_passwords(selected, environ)


def response_bytes(data):
    """All bytes of an imaplib response, joined, for regex searches."""
    parts = []
    for item in data or []:
        if isinstance(item, tuple):
            parts.extend(part for part in item if isinstance(part, bytes))
        elif isinstance(item, bytes):
            parts.append(item)
    return b" ".join(parts)


def response_for(uid, data):
    """
    The FETCH response line for one UID. Other lines may be unsolicited flag
    updates for other messages, which must not be read as this one's.
    """
    for item in data or []:
        line = item[0] if isinstance(item, tuple) else item
        if isinstance(line, bytes) and re.search(rb"\bUID " + uid + rb"\b", line):
            return response_bytes([item])
    return b""


def quoted(mailbox):
    return f'"{mailbox}"'


class LiveServer:
    """The harness's own connection: setup, inspection, and cleanup."""

    def __init__(self, cfg):
        self.imap = imaplib.IMAP4_SSL(cfg["imap_host"], timeout=60)
        self.imap.login(cfg["username"], mail_filter.account_password(cfg))
        self.is_gmail = mail_filter.detect_gmail(self.imap)
        status, data = self.imap.capability()
        self.uidplus = status == "OK" and b"UIDPLUS" in response_bytes(data).upper().split()
        self.trash, self.trash_problem = mail_filter.find_advertised_trash(self.imap)
        self.all_mail = self.mailbox_with_attribute(b"\\all")
        self.prefix, self.delimiter = self.namespace()
        self.created = []  # folders this run created, children first
        self.notes = []  # things the tests could not clean up

    def namespace(self):
        """(prefix, delimiter) of the personal namespace, e.g. ("INBOX.", ".")."""
        try:
            status, data = self.imap.namespace()
        except imaplib.IMAP4.error:
            status, data = "NO", None
        if status == "OK":
            match = re.match(rb'\(\("((?:[^"\\]|\\.)*)" (?:"(\\?.)"|NIL)\)', response_bytes(data))
            if match:
                delimiter = match.group(2)
                return match.group(1).decode(), delimiter.decode()[-1] if delimiter else None
        status, data = self.imap.list('""', '""')
        match = re.search(rb'\) "(\\?.)" ', response_bytes(data)) if status == "OK" else None
        return "", match.group(1).decode()[-1] if match else None

    def mailbox_with_attribute(self, attribute):
        status, entries = self.imap.list()
        if status != "OK":
            return None
        for entry in entries or []:
            parsed = mail_filter.parse_list_entry(entry)
            if parsed and attribute in parsed[0].lower().split():
                return parsed[1].decode("ascii", errors="replace")
        return None

    def folder(self, name):
        """A test folder's full mailbox name, e.g. "INBOX.mail-filter-live-tests.moved"."""
        if not name:
            return self.prefix + TEST_ROOT
        if self.delimiter is None:  # flat server: no hierarchy
            return f"{self.prefix}{TEST_ROOT}-{name}"
        return f"{self.prefix}{TEST_ROOT}{self.delimiter}{name}"

    def exists(self, mailbox):
        status, entries = self.imap.list('""', quoted(mailbox))
        return status == "OK" and any(mail_filter.parse_list_entry(e) for e in entries or [])

    def create_folders(self, names):
        parent = self.folder(None)
        parent_existed = self.delimiter is None or self.exists(parent)
        if not parent_existed:
            self.create(parent)
        for name in names:
            mailbox = self.folder(name)
            if not self.exists(mailbox):
                self.create(mailbox)
                self.created.insert(0, mailbox)
        if not parent_existed:
            self.created.append(parent)

    def create(self, mailbox):
        status, data = self.imap.create(quoted(mailbox))
        if status != "OK" and not self.exists(mailbox):
            raise AssertionError(f"cannot create '{mailbox}' (status={status}, {data})")

    def append(self, message):
        status, data = self.imap.append("INBOX", None, None, message.as_bytes(policy=SMTP))
        if status != "OK":
            raise AssertionError(f"APPEND to INBOX failed (status={status}, {data})")

    def messages_in(self, mailbox, query, readonly=True):
        """
        {Message-ID: Found} for the test messages in a mailbox whose subject
        contains the query, or None if the mailbox cannot be selected.
        """
        status, _ = self.imap.select(quoted(mailbox), readonly=readonly)
        if status != "OK":
            return None
        status, data = self.imap.uid("SEARCH", "SUBJECT", quoted(query))
        if status != "OK":
            raise AssertionError(f"SEARCH in '{mailbox}' failed (status={status})")
        found = {}
        for uid in (data[0] or b"").split():
            status, data = self.imap.uid(
                "FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID X-MAIL-FILTER-TEST)])")
            headers = email.message_from_bytes(mail_filter.fetched_body(data) or b"")
            test_id = (headers.get(mail_filter.TEST_HEADER) or "").strip()
            if status != "OK" or not test_id:
                continue  # not a test message: never touched
            status, data = self.imap.uid("FETCH", uid, "(FLAGS)")
            flags = re.search(rb"FLAGS \(([^)]*)\)", response_for(uid, data))
            labels = b""
            if self.is_gmail:
                status, data = self.imap.uid("FETCH", uid, "(X-GM-LABELS)")
                match = re.search(rb"X-GM-LABELS \((.*?)\)(?: UID|\)|$)", response_for(uid, data))
                labels = match.group(1) if match else b""
            found[(headers.get("Message-ID") or "").strip()] = Found(
                uid, test_id, set(flags.group(1).split()) if flags else set(), labels)
        return found

    def delete_test_messages(self, query, accept):
        """
        Permanently delete the test messages whose test ID passes accept().
        Returns how many were deleted.
        """
        deleted = 0
        places = [self.folder("moved"), self.folder("alt-trash")]
        if self.is_gmail:
            # Every message except Trash and Spam is in All Mail. Gmail deletes
            # permanently only from Trash, so move there first.
            for mailbox in [self.all_mail] if self.all_mail else ["INBOX"] + places:
                found = self.accepted(mailbox, query, accept)
                if found and self.trash:
                    self.imap.uid("COPY", b",".join(f.uid for f in found), quoted(self.trash))
                elif found:
                    self.notes.append(f"{len(found)} test message(s) left in '{mailbox}': "
                                      f"no Trash mailbox ({self.trash_problem})")
            places = []
        for mailbox in ["INBOX"] + places + [self.trash]:
            found = self.accepted(mailbox, query, accept) if mailbox else None
            if not found:
                continue
            uids = b",".join(f.uid for f in found)
            self.imap.uid("STORE", uids, "+FLAGS", "(\\Seen \\Deleted)")
            if self.uidplus:
                self.imap.uid("EXPUNGE", uids)
            elif mailbox in self.created:
                self.imap.expunge()
            else:
                # A plain EXPUNGE here would also remove other \Deleted mail
                self.notes.append(f"{len(found)} test message(s) in '{mailbox}' marked "
                                  "\\Deleted but not expunged: server lacks UIDPLUS")
                continue
            deleted += len(found)
        return deleted

    def accepted(self, mailbox, query, accept):
        found = self.messages_in(mailbox, query, readonly=False)
        return [f for f in (found or {}).values() if accept(f.test_id)]

    def message_count(self, mailbox):
        """Messages in a mailbox, or None if it cannot be selected (e.g. \\Noselect)."""
        status, data = self.imap.select(quoted(mailbox), readonly=True)
        if status != "OK":
            return None
        try:
            return int(data[0])
        except (TypeError, ValueError, IndexError):
            return None

    def delete_empty_test_folders(self):
        """
        Delete the harness's own folders that are empty, whichever run created
        them. The mail-filter-live-tests name belongs only to these tests.
        Returns the folders deleted.
        """
        deleted = []
        children = [self.folder("moved"), self.folder("alt-trash")]
        parent = [] if self.delimiter is None else [self.folder(None)]
        kept = False
        for mailbox in children + parent:
            if not self.exists(mailbox):
                continue
            if mailbox in parent and kept:
                self.notes.append(f"kept folder '{mailbox}': it still has subfolders")
                continue
            if self.message_count(mailbox):
                # e.g. a recent --keep run's messages, swept after an hour
                self.notes.append(f"kept folder '{mailbox}': not empty")
                kept = True
                continue
            self.imap.select("INBOX", readonly=True)  # never delete the selected mailbox
            status, data = self.imap.delete(quoted(mailbox))
            if status != "OK":
                self.notes.append(f"could not delete folder '{mailbox}' (status={status}, {data})")
                kept = True
            else:
                deleted.append(mailbox)
        self.created = []
        return deleted

    def test_folders(self):
        """The harness's folders that exist now."""
        names = [self.folder("moved"), self.folder("alt-trash")]
        if self.delimiter is not None:
            names.append(self.folder(None))
        return [name for name in names if self.exists(name)]

    def logout(self):
        try:
            self.imap.logout()
        except (imaplib.IMAP4.error, OSError):
            pass


# A test case: (name, rule "do", or None for no rule). "headers" gets its own
# match below; every other rule matches only its own message by subject.
BATCHES = {
    "a": [
        ("move", {"move": "moved"}),
        ("moveread", {"move": "moved", "mark_read": True}),
        ("trash", {"trash": True}),
        ("delete", {"delete": True}),
        ("markread", {"mark_read": True}),
        ("noaction", {}),
        ("norule", None),
        ("nomapping", {"move": "unmapped"}),
        ("nofolder", {"move": "missing"}),
        ("headers", {"mark_read": True}),
    ],
    "b": [("trashmap", {"trash": True})],
    "c": [("drymove", {"move": "moved"}), ("drytrash", {"trash": True}),
          ("drydelete", {"delete": True})],
    "d": [("ignored", None)],
}
HEADERS_FROM = ("Zoë Tester", "zoe@example.net")
HEADERS_TO = "mf-live-headers@example.com"


class LiveAccountMixin:
    """Live tests for one account; subclassed per account below."""

    ACCOUNT_ID = None
    CFG = None

    @classmethod
    def setUpClass(cls):
        cls.run_id = new_run_id()
        cls.logs = {}
        cls.subjects = {}
        cls.message_ids = {}
        cls.server = LiveServer(cls.CFG)
        cls.addClassCleanup(cls.cleanup)
        server = cls.server

        swept = server.delete_test_messages(SWEEP_QUERY, is_stale)
        if swept:
            cls.say(f"Removed {swept} test message(s) left by earlier runs")
        server.create_folders(["moved", "alt-trash"])
        cls.moved = server.folder("moved")
        cls.alt = server.folder("alt-trash")
        cls.missing = server.folder("does-not-exist")
        if server.exists(cls.missing):
            raise AssertionError(f"'{cls.missing}' exists; the nofolder case needs it missing")

        cls.recording = []
        cls.scrubber = imap_recording.Scrubber(
            cls.CFG["username"], cls.CFG["imap_host"], cls.ACCOUNT_ID,
            keep_prefixes=[server.folder(None)])

        # Each batch sets dry_run itself, whatever the account's own setting
        account = {"id": cls.ACCOUNT_ID, **cls.CFG, "dry_run": False}
        folders = {"moved": cls.moved, "missing": cls.missing}
        cls.run_batch("a", account, folders)
        cls.run_batch("b", account, {"trash": cls.alt})
        cls.run_batch("c", {**account, "dry_run": True}, {"moved": cls.moved})
        cls.run_batch("d", account, {"moved": cls.moved}, normal_run=True)
        if RECORD:
            cls.write_recording()

    @classmethod
    def write_recording(cls):
        """Save the filter's scrubbed IMAP conversations for offline replay."""
        name = "gmail" if cls.server.is_gmail else "imap"
        number = 2
        while name in RECORDED_THIS_RUN:  # two accounts of the same kind
            name = f"{name.split('-')[0]}-{number}"
            number += 1
        RECORDED_THIS_RUN.add(name)
        path = FIXTURES / f"{name}.txt"
        imap_recording.write_recording(path, name, cls.recording)
        exchanges = sum(len(batch["exchanges"]) for batch in cls.recording)
        shown = path.relative_to(BASE) if BASE in path.parents else path
        cls.say(f"Recorded {exchanges} IMAP exchanges to {shown} "
                f"({len(cls.scrubber.renamed)} mailbox name(s) replaced); "
                "review it before committing")

    @classmethod
    def cleanup(cls):
        server = cls.server
        try:
            if KEEP:
                folders = ", ".join(server.test_folders()) or "none"
                cls.say(f"Keeping test run {cls.run_id}: its messages and the test "
                        f"folders are left in place (folders: {folders})")
            else:
                cls.say(f"Cleaning up test run {cls.run_id}: deleting its messages "
                        "and the empty test folders")
                count = server.delete_test_messages(
                    cls.run_id, lambda t: t.startswith(cls.run_id))
                folders = ", ".join(server.delete_empty_test_folders()) or "none"
                cls.say(f"Cleanup done: deleted {count} test message(s); "
                        f"deleted folders: {folders}")
            for note in server.notes:
                cls.say(f"Warning: {note}")
        finally:
            server.logout()

    @classmethod
    def say(cls, text):
        """Harness progress, on stderr beside unittest's own output."""
        print(f"[{cls.ACCOUNT_ID}] {text}", file=sys.stderr)

    @classmethod
    def run_batch(cls, batch, account, folders, normal_run=False):
        """APPEND the batch's messages, then run the real filter on them."""
        batch_id = f"{cls.run_id}x{batch}"
        rules = {"rules": []}
        for case, do in BATCHES[batch]:
            subject = f"{MARKER} {cls.run_id} {batch_id} {case} live test"
            message_id = f"<{batch_id}.{case}@mail-filter.invalid>"
            cls.subjects[case] = subject
            cls.message_ids[case] = message_id
            from_name, from_addr = HEADERS_FROM if case == "headers" else (
                "mail-filter live test", "live-test@example.org")
            message = EmailMessage()
            message["From"] = Address(from_name, addr_spec=from_addr)
            message["To"] = HEADERS_TO if case == "headers" else "live-test@example.org"
            message["Subject"] = subject
            message["Date"] = formatdate(localtime=True)
            message["Message-ID"] = message_id
            message[mail_filter.TEST_HEADER] = batch_id
            message.set_content(
                "Created by mail-filter's live IMAP tests (imap_tests.py); safe to delete.\n")
            cls.server.append(message)

            match = {"subject_contains": f"{batch_id} {case} "}
            if case == "headers":
                match = {
                    **match,
                    "to_local_is": HEADERS_TO.partition("@")[0],
                    "from_name_is": HEADERS_FROM[0],
                    "from_email_is": HEADERS_FROM[1],
                }
            if do is not None:
                rules["rules"].append({"name": case, "match": match, "do": do})
        if normal_run:
            rules["catch_all"] = {"move": "moved"}

        cls.wait_for_inbox(batch_id, len(BATCHES[batch]))
        # A normal run (no test run ID) must ignore test messages. Narrow its
        # search to this batch so no real mail is ever fetched.
        imap_class = narrowed_imap(batch_id) if normal_run else imaplib.IMAP4_SSL
        recorder = imap_recording.Recorder(cls.scrubber)
        if RECORD:
            # Records what the filter sends, above the narrowing
            imap_class = recorder.imap_class(imap_class)
        test_run_id = None if normal_run else batch_id
        out = io.StringIO()
        with redirect_stdout(out), \
                mock.patch.object(mail_filter.imaplib, "IMAP4_SSL", imap_class):
            mail_filter.process_account(account, folders, rules, test_run_id=test_run_id)
        cls.logs[batch] = [line.split("] ", 1)[-1] for line in out.getvalue().splitlines()]
        cls.recording.append({
            "batch": batch,
            "test_run_id": test_run_id,
            "dry_run": account["dry_run"],
            "folders": folders,
            "rules": rules,
            "log": [cls.scrubber.log_line(line) for line in out.getvalue().splitlines()],
            "exchanges": recorder.exchanges,
        })
        with open(LOG_FILE, "a") as log_file:
            log_file.write(f"# {cls.ACCOUNT_ID} batch {batch} ({batch_id})\n{out.getvalue()}")

    @classmethod
    def wait_for_inbox(cls, batch_id, count):
        deadline = time.monotonic() + WAIT_SECONDS
        while True:
            found = cls.server.messages_in("INBOX", batch_id) or {}
            if len(found) >= count:
                return
            if time.monotonic() > deadline:
                raise AssertionError(
                    f"only {len(found)} of {count} appended messages found in INBOX")
            time.sleep(1)

    # ---------- assertions ----------

    def log_text(self, case):
        """The filter's log line(s) for one case; the full log is in LOG_FILE."""
        subject = f" SUBJECT='{self.subjects[case]}'"
        lines = [line for lines in self.logs.values() for line in lines if subject in line]
        return "\n".join(lines or ["(no log line)"]) + f"\n(full filter log: {LOG_FILE.name})"

    def line_for(self, case):
        subject = self.subjects[case]
        lines = [line for lines in self.logs.values() for line in lines
                 if f" SUBJECT='{subject}'" in line]
        self.assertEqual(len(lines), 1, f"expected one line for {case}:\n{self.log_text(case)}")
        return lines[0]

    def assert_matched(self, case, outcome, regex=False):
        outcome = outcome if regex else re.escape(outcome)
        self.assertRegex(
            self.line_for(case),
            rf"^\[{re.escape(self.ACCOUNT_ID)}\] MATCHED #\d+ RULE='{case}' "
            rf"SUBJECT='{re.escape(self.subjects[case])}' {outcome}$")

    def find(self, case, mailbox, present=True):
        """Wait for the message to be present in (or absent from) a mailbox."""
        message_id = self.message_ids[case]
        deadline = time.monotonic() + WAIT_SECONDS
        while True:
            found = self.server.messages_in(mailbox, self.run_id)
            self.assertIsNotNone(found, f"cannot select '{mailbox}'")
            if (message_id in found) == present:
                return found.get(message_id)
            if time.monotonic() > deadline:
                state = ""
                if message_id in found:
                    f = found[message_id]
                    state = f"\nflags: {b' '.join(sorted(f.flags)).decode()}"
                    if self.server.is_gmail:
                        state += f"\nX-GM-LABELS: {f.labels.decode(errors='replace')}"
                self.fail(f"{case}: expected {'in' if present else 'not in'} "
                          f"'{mailbox}' after {WAIT_SECONDS} s{state}\n{self.log_text(case)}")
            time.sleep(1)

    def assert_left_in_inbox(self, case, seen=False):
        found = self.find(case, "INBOX")
        self.assertEqual("\\Seen" in {f.decode() for f in found.flags}, seen,
                         f"{case}: \\Seen flag in INBOX")
        # A stray \Deleted would lose the message at the next EXPUNGE,
        # ours or a mail client's (a dry run never expunges)
        self.assertNotIn(b"\\Deleted", found.flags, f"{case}: \\Deleted flag in INBOX")

    def assert_moved(self, case, mailbox, seen=False):
        found = self.find(case, mailbox)
        self.assertEqual("\\Seen" in {f.decode() for f in found.flags}, seen,
                         f"{case}: \\Seen flag in '{mailbox}'")
        # COPY must come before \Deleted is set on the original, or the copy
        # carries \Deleted and a later EXPUNGE removes the only copy
        self.assertNotIn(b"\\Deleted", found.flags, f"{case}: \\Deleted flag in '{mailbox}'")
        self.find(case, "INBOX", present=False)
        # Gmail: the folder is a label, and \Inbox is gone. (Trash is not in All Mail.)
        if self.server.is_gmail and self.server.all_mail and mailbox != self.server.trash:
            labels = self.find(case, self.server.all_mail).labels
            self.assertNotIn(b"\\Inbox", labels, f"{case}: Gmail labels {labels!r}")
            self.assertIn(mailbox.encode(), labels, f"{case}: Gmail labels {labels!r}")

    # ---------- batch a: one message per action ----------

    def test_move(self):
        self.assert_matched("move", f"→ {self.moved}")
        self.assert_moved("move", self.moved)

    def test_move_with_mark_read(self):
        self.assert_matched("moveread", f"→ {self.moved} (mark_read)")
        self.assert_moved("moveread", self.moved, seen=True)

    def test_trash_to_server_trash(self):
        if not self.server.trash:
            self.skipTest(f"server has no \\Trash mailbox ({self.server.trash_problem})")
        self.assert_matched("trash", f"→ Trash ({self.server.trash})")
        self.assert_moved("trash", self.server.trash)

    def test_trash_to_mapped_trash(self):
        self.assert_matched("trashmap", f"→ Trash ({self.alt})")
        self.assert_moved("trashmap", self.alt)

    def test_delete(self):
        self.assert_matched("delete", "DELETED")
        self.find("delete", "INBOX", present=False)

    def test_mark_read_only(self):
        self.assert_matched("markread", "MARKED_READ")
        self.assert_left_in_inbox("markread", seen=True)

    def test_rule_without_action(self):
        self.assert_matched("noaction", "NO_ACTION")
        self.assert_left_in_inbox("noaction")

    def test_no_rule(self):
        self.assertRegex(
            self.line_for("norule"),
            rf"^\[{re.escape(self.ACCOUNT_ID)}\] NO_RULE #\d+ "
            rf"SUBJECT='{re.escape(self.subjects['norule'])}'$")
        self.assert_left_in_inbox("norule")

    def test_missing_folder_mapping_is_skipped(self):
        self.assert_matched("nomapping", "SKIPPED: no folder mapping for key 'unmapped'")
        self.assert_left_in_inbox("nomapping")

    def test_missing_mailbox_fails_safely(self):
        # Gmail uses UID MOVE, other servers COPY (ADR 006)
        verb = "move" if self.server.is_gmail else "copy"
        self.assert_matched(
            "nofolder",
            rf"FAILED: {verb} to '{re.escape(self.missing)}' refused \(status=\w+\); "
            "left in INBOX",
            regex=True)
        self.assert_left_in_inbox("nofolder")

    def test_headers_decoded_from_real_server(self):
        # to_local, a MIME-encoded non-ASCII from_name, and from_email all match
        self.assert_matched("headers", "MARKED_READ")
        self.assert_left_in_inbox("headers", seen=True)

    # ---------- batch c: dry run ----------

    def test_dry_run_changes_nothing(self):
        self.assertIn(f"[{self.ACCOUNT_ID}] dry_run is true; no changes will be made on the server",
                      self.logs["c"])
        self.assertNotIn("dry_run", "\n".join(self.logs["a"] + self.logs["b"] + self.logs["d"]))
        for case in ("drymove", "drytrash", "drydelete"):
            with self.subTest(case=case):
                self.assertTrue(self.line_for(case).endswith(" [DRY_RUN]"), self.line_for(case))
                self.assert_left_in_inbox(case)

    # ---------- batch d: a normal run ignores test messages ----------

    def test_normal_run_ignores_test_messages(self):
        self.assertRegex(
            self.line_for("ignored"),
            rf"^\[{re.escape(self.ACCOUNT_ID)}\] IGNORED #\d+ "
            rf"SUBJECT='{re.escape(self.subjects['ignored'])}'$")
        self.assert_left_in_inbox("ignored")


def narrowed_imap(batch_id):
    """An IMAP4_SSL whose SEARCH finds only this batch's messages."""

    class NarrowedIMAP(imaplib.IMAP4_SSL):
        def uid(self, command, *args):
            if command.upper() == "SEARCH":
                return super().uid("SEARCH", "UNSEEN", "SUBJECT", quoted(batch_id))
            return super().uid(command, *args)

    return NarrowedIMAP


ACCOUNTS, CONFIG_PROBLEMS = load_live_accounts(os.environ) if LIVE else ({}, [])

if not LIVE:
    @unittest.skip("live IMAP tests run only through imap_tests.py (see docs/development.md)")
    class LiveIMAPTests(unittest.TestCase):
        def test_live_imap(self):
            pass
elif CONFIG_PROBLEMS:
    class LiveIMAPConfigTests(unittest.TestCase):
        def test_configuration(self):
            self.fail("\n".join(CONFIG_PROBLEMS))
else:
    for _account_id, _cfg in ACCOUNTS.items():
        _name = "LiveIMAP_" + re.sub(r"\W", "_", _account_id)
        globals()[_name] = type(_name, (LiveAccountMixin, unittest.TestCase),
                                {"ACCOUNT_ID": _account_id, "CFG": _cfg})


if __name__ == "__main__":
    unittest.main()
