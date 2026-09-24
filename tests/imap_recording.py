"""
Record the filter's IMAP conversations on real servers, and replay them offline.

The live IMAP tests (imap_tests.py --record) wrap the filter's own connection,
save every command it sends with the reply imaplib returned, and write them,
scrubbed, to tests/fixtures/imap/<server>.txt. test_recorded_imap.py replays
each recording through process_account and requires the log the live run
produced. Replies are looked up by the exact command, not by position.

Scrubbing: the recording never contains the password (login is not recorded),
and replaces the username, the IMAP host, the account ID, and every mailbox
name in LIST replies except INBOX, special-use mailboxes (\\Trash, \\All, ...),
and the test folders.
"""
import ast
import imaplib
import pprint
import re
from collections import defaultdict, deque
from datetime import date

import mail_filter

# Stand-ins for the recorded account's identifying details
PLACEHOLDER_USER = "user@example.com"
PLACEHOLDER_HOST = "imap.example.com"
PLACEHOLDER_ACCOUNT = "test"

SPECIAL_USE = {b"\\all", b"\\archive", b"\\drafts", b"\\flagged", b"\\important",
               b"\\junk", b"\\sent", b"\\trash"}


class Scrubber:
    """Replaces identifying strings in recorded commands and replies."""

    def __init__(self, username, host, account_id, keep_prefixes):
        # Whole values only, e.g. "me@example.net" but not "me" inside "message"
        self.patterns = [
            (re.compile(r"(?<![\w.@-])" + re.escape(old) + r"(?![\w.@-])"), new)
            for old, new in [(username, PLACEHOLDER_USER), (host, PLACEHOLDER_HOST)] if old
        ]
        self.account_id = account_id
        self.keep_prefixes = [p.encode() for p in keep_prefixes]
        self.renamed = {}  # real mailbox name -> placeholder

    def value(self, value):
        """Scrub one recorded value: bytes, str, tuple, list, or None."""
        if isinstance(value, bytes):
            text = value.decode("latin-1")  # every byte maps to one character
            return self.value(text).encode("latin-1")
        if isinstance(value, str):
            for pattern, new in self.patterns:
                value = pattern.sub(new, value)
            return value
        if isinstance(value, (tuple, list)):
            return type(value)(self.value(item) for item in value)
        return value

    def list_reply(self, reply):
        """Scrub a LIST reply: rename every mailbox a reader need not see."""
        status, entries = reply
        scrubbed = []
        for entry in entries or []:
            parsed = mail_filter.parse_list_entry(entry)
            if parsed is None:
                scrubbed.append(entry)
                continue
            attrs, name = parsed
            if not self.keeps(attrs, name):
                name = self.renamed.setdefault(name, b"Folder-%d" % (len(self.renamed) + 1))
            head = entry[0] if isinstance(entry, tuple) else entry
            delimiter = re.match(rb'^\([^)]*\) ("(?:[^"\\]|\\.)*"|NIL) ', head).group(1)
            scrubbed.append(b"(" + attrs + b") " + delimiter + b' "' + name + b'"')
        return status, scrubbed

    def keeps(self, attrs, name):
        if name.upper() == b"INBOX" or name == b"[Gmail]" or name == b"[Google Mail]":
            return True
        if SPECIAL_USE & set(attrs.lower().split()):
            return True
        return any(name.startswith(prefix) for prefix in self.keep_prefixes)

    def log_line(self, line):
        """A log line without its timestamp, with placeholders."""
        line = line.split("] ", 1)[-1]
        line = line.replace(f"[{self.account_id}]", f"[{PLACEHOLDER_ACCOUNT}]")
        return self.value(line)


class Recorder:
    """Collects (method, args, reply) exchanges from a recording connection."""

    def __init__(self, scrubber):
        self.scrubber = scrubber
        self.exchanges = []

    def add(self, method, args, reply):
        if method == "list":
            reply = self.scrubber.list_reply(reply)
        self.exchanges.append(
            (method, self.scrubber.value(args), self.scrubber.value(reply)))

    def imap_class(self, base=imaplib.IMAP4_SSL):
        """An IMAP4_SSL subclass (of base) that records the calls the filter makes."""
        recorder = self

        class RecordingIMAP(base):
            # imaplib asks for capabilities while connecting, before login;
            # record only what the filter itself sends after login.
            recording = False

            def login(self, user, password):
                reply = super().login(user, password)
                self.recording = True
                return reply

            def capability(self):
                reply = super().capability()
                if self.recording:
                    recorder.add("capability", (), reply)
                return reply

            def select(self, mailbox="INBOX", readonly=False):
                reply = super().select(mailbox, readonly)
                recorder.add("select", (mailbox, readonly), reply)
                return reply

            def list(self, directory='""', pattern="*"):
                reply = super().list(directory, pattern)
                recorder.add("list", (directory, pattern), reply)
                return reply

            def uid(self, command, *args):
                reply = super().uid(command, *args)
                recorder.add("uid", (command,) + args, reply)
                return reply

            def expunge(self):
                reply = super().expunge()
                recorder.add("expunge", (), reply)
                return reply

        return RecordingIMAP


class RecordedIMAP:
    """
    Replays a recording as an IMAP4_SSL stand-in. Each command is answered with
    the reply recorded for exactly that command; repeated commands get their
    recorded replies in order, then the last one again.
    """

    def __init__(self, exchanges):
        self.replies = defaultdict(deque)
        for method, args, reply in exchanges:
            self.replies[(method, tuple(args))].append(reply)

    def __call__(self, host, *args, **kwargs):
        return self

    def answer(self, method, *args):
        replies = self.replies.get((method, args))
        if not replies:
            raise AssertionError(
                f"IMAP command not in the recording: {method}{args!r}. The filter now "
                "sends a command the server was never asked; record again with "
                "imap_tests.py --record (see docs/development.md#recorded-imap-replies)")
        return replies.popleft() if len(replies) > 1 else replies[0]

    def login(self, user, password):
        return "OK", [b"LOGIN completed"]

    def logout(self):
        return "BYE", [b"LOGOUT"]

    def capability(self):
        return self.answer("capability")

    def select(self, mailbox="INBOX", readonly=False):
        return self.answer("select", mailbox, readonly)

    def list(self, directory='""', pattern="*"):
        return self.answer("list", directory, pattern)

    def uid(self, command, *args):
        return self.answer("uid", command, *args)

    def expunge(self):
        return self.answer("expunge")


# Addresses allowed in a recording: placeholders, test senders, test Message-IDs
ALLOWED_ADDRESS_DOMAINS = ("example.com", "example.org", "example.net", "mail-filter.invalid")
ADDRESS = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")
PLACEHOLDER_FOLDER = re.compile(rb"^Folder-\d+$")
TEST_FOLDER_ROOT = b"mail-filter-live-tests"


def recording_problems(path, accounts):
    """
    Return a message for everything in a recording that may identify its
    owner (empty if none). accounts is accounts.local.json5, whose usernames,
    hosts, and account IDs must not appear.
    """
    text = path.read_text(encoding="latin-1")
    problems = []

    needles = {}
    for account_id, cfg in accounts.items():
        username = cfg.get("username", "") if isinstance(cfg, dict) else ""
        host = cfg.get("imap_host", "") if isinstance(cfg, dict) else ""
        local, _, domain = username.partition("@")
        for value, what in [(account_id, "account ID"), (username, "username"),
                            (local, "username"), (domain, "username domain"),
                            (host, "IMAP host")]:
            if len(value) >= 3 and value not in (PLACEHOLDER_ACCOUNT, "gmail.com"):
                needles.setdefault(value, f"{what} of account '{account_id}'")
    for value, what in sorted(needles.items()):
        # A whole word: "jane" in "jane@corp.example", not in "janet"
        pattern = r"(?<![\w.-])" + re.escape(value) + r"(?![\w-])"
        if re.search(pattern, text, re.IGNORECASE):
            problems.append(f"contains the {what}: {value!r}")

    # pprint may split a long string over two lines; addresses are checked
    # in the recording's values, not its layout
    recording = read_recording(path)
    strings = []

    def collect(value):
        if isinstance(value, (bytes, str)):
            strings.append(value.decode("latin-1") if isinstance(value, bytes) else value)
        elif isinstance(value, (tuple, list)):
            for item in value:
                collect(item)
        elif isinstance(value, dict):
            for item in value.values():
                collect(item)
    collect(recording)
    for domain in sorted({m.group(1).lower() for s in strings for m in ADDRESS.finditer(s)}):
        if not domain.endswith(ALLOWED_ADDRESS_DOMAINS):
            problems.append(f"contains an address at '{domain}'")

    for batch in recording["batches"]:
        for method, _, reply in batch["exchanges"]:
            if method != "list":
                continue
            for entry in reply[1] or []:
                parsed = mail_filter.parse_list_entry(entry)
                if parsed and not folder_name_allowed(*parsed):
                    problems.append(f"lists a personal mailbox name: {parsed[1]!r}")
    return sorted(set(problems))


def folder_name_allowed(attrs, name):
    """INBOX, special-use and test mailboxes, and scrubbed placeholders."""
    if name.upper() == b"INBOX" or name in (b"[Gmail]", b"[Google Mail]"):
        return True
    if SPECIAL_USE & set(attrs.lower().split()):
        return True
    return TEST_FOLDER_ROOT in name or bool(PLACEHOLDER_FOLDER.match(name))


def write_recording(path, server, batches):
    """Write a recording as a Python literal (read back with ast.literal_eval)."""
    recording = {"server": server, "recorded": date.today().isoformat(), "batches": batches}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# IMAP replies recorded from a real server by imap_tests.py --record.\n"
        "# Replayed offline by tests/test_recorded_imap.py. Scrubbed of the username,\n"
        "# host, account ID, and personal mailbox names; review before committing.\n"
        + pprint.pformat(recording, width=100, sort_dicts=False) + "\n")


def read_recording(path):
    return ast.literal_eval(path.read_text())
