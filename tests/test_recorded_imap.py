"""
Replay IMAP conversations recorded from real servers (imap_tests.py --record).

Each recording holds the filter's commands and the real server's replies for
one live test run. process_account runs offline against those replies and must
produce the same log the live run did. This catches parsing assumptions that a
hand-written fake cannot, such as unsolicited FETCH responses or the exact
replies Gmail gives to MOVE.
"""
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import mail_filter  # noqa: E402
import imap_recording  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "imap"
ACCOUNT = {
    "id": imap_recording.PLACEHOLDER_ACCOUNT,
    "imap_host": imap_recording.PLACEHOLDER_HOST,
    "username": imap_recording.PLACEHOLDER_USER,
    "password": "unused",
}


def replay(batch):
    """Run process_account against one recorded batch; return its log lines."""
    fake = imap_recording.RecordedIMAP(batch["exchanges"])
    out = io.StringIO()
    with mock.patch.object(mail_filter.imaplib, "IMAP4_SSL", fake), \
            mock.patch.object(mail_filter, "DRY_RUN", batch["dry_run"]), \
            redirect_stdout(out):
        mail_filter.process_account(
            ACCOUNT, batch["folders"], batch["rules"], test_run_id=batch["test_run_id"])
    return [line.split("] ", 1)[-1] for line in out.getvalue().splitlines()]


class RecordedIMAPTests(unittest.TestCase):

    def test_recordings_replay_to_the_recorded_log(self):
        paths = sorted(FIXTURES.glob("*.txt"))
        if not paths:
            self.skipTest("no recordings in tests/fixtures/imap/")
        for path in paths:
            recording = imap_recording.read_recording(path)
            for batch in recording["batches"]:
                with self.subTest(server=recording["server"], batch=batch["batch"]):
                    self.assertEqual(replay(batch), batch["log"])


class RecordingMachineryTests(unittest.TestCase):
    """The recorder, scrubber, and replayer themselves, without a server."""

    def scrubber(self):
        return imap_recording.Scrubber(
            "me@private.example", "mail.private.example", "my-account",
            keep_prefixes=["INBOX.mail-filter-live-tests"])

    def test_list_reply_keeps_only_standard_and_test_mailboxes(self):
        reply = ("OK", [
            b'(\\HasChildren) "." INBOX',
            b'(\\HasNoChildren) "." "INBOX.Taxes 2025"',
            b'(\\HasNoChildren \\Trash) "." INBOX.Trash',
            b'(\\HasNoChildren) "." INBOX.mail-filter-live-tests.moved',
            (b'(\\HasNoChildren) "." {9}', b'Corbeille'),
            b'(\\HasNoChildren) "." "INBOX.Taxes 2025"',
        ])
        scrubber = self.scrubber()
        self.assertEqual(scrubber.list_reply(reply), ("OK", [
            b'(\\HasChildren) "." "INBOX"',
            b'(\\HasNoChildren) "." "Folder-1"',
            b'(\\HasNoChildren \\Trash) "." "INBOX.Trash"',
            b'(\\HasNoChildren) "." "INBOX.mail-filter-live-tests.moved"',
            b'(\\HasNoChildren) "." "Folder-2"',
            b'(\\HasNoChildren) "." "Folder-1"',
        ]))

    def test_identifying_strings_are_replaced_everywhere(self):
        scrubber = self.scrubber()
        self.assertEqual(
            scrubber.value(("OK", [(b"me@private.example on mail.private.example", None)])),
            ("OK", [(b"user@example.com on imap.example.com", None)]))
        self.assertEqual(
            scrubber.log_line("[2026-09-24 10:00:00] [my-account] Connecting to "
                              "mail.private.example as me@private.example"),
            "[test] Connecting to imap.example.com as user@example.com")

    def test_only_whole_values_are_replaced(self):
        scrubber = imap_recording.Scrubber("u", "h", "a", keep_prefixes=[])
        self.assertEqual(scrubber.value("Found 3 unseen messages as u on h"),
                         "Found 3 unseen messages as user@example.com on imap.example.com")
        self.assertEqual(scrubber.value(b"\xff u \xfe"), b"\xff user@example.com \xfe")

    def test_empty_values_are_not_replaced(self):
        scrubber = imap_recording.Scrubber("", "", "a", keep_prefixes=[])
        self.assertEqual(scrubber.value(b"abc"), b"abc")

    def test_capabilities_asked_before_login_are_not_recorded(self):
        class Server:  # like imaplib: asks for capabilities while connecting
            def __init__(self, host):
                self.logged_in = False
                self.capability()

            def login(self, user, password):
                self.logged_in = True
                return "OK", [b""]

            def capability(self):
                return "OK", [b"IMAP4rev1 MOVE" if self.logged_in else b"IMAP4rev1"]

        recorder = imap_recording.Recorder(self.scrubber())
        imap = recorder.imap_class(Server)("host")
        imap.login("me@private.example", "secret")
        imap.capability()
        self.assertEqual(recorder.exchanges,
                         [("capability", (), ("OK", [b"IMAP4rev1 MOVE"]))])

    def test_replay_answers_by_command_and_in_order(self):
        fake = imap_recording.RecordedIMAP([
            ("select", ("INBOX", False), ("OK", [b"2"])),
            ("select", ("INBOX", False), ("OK", [b"3"])),
            ("capability", (), ("OK", [b"IMAP4rev1"])),
        ])
        self.assertEqual(fake.capability(), ("OK", [b"IMAP4rev1"]))
        self.assertEqual(fake.select("INBOX"), ("OK", [b"2"]))
        self.assertEqual(fake.select("INBOX"), ("OK", [b"3"]))
        self.assertEqual(fake.select("INBOX"), ("OK", [b"3"]))  # last reply repeats
        with self.assertRaisesRegex(AssertionError, "not in the recording"):
            fake.uid("SEARCH", "UNSEEN")

    def test_written_recording_reads_back_identically(self):
        batches = [{"batch": "a", "log": ["[test] Done"],
                    "exchanges": [("uid", ("FETCH", b"7", "(BODY.PEEK[])"),
                                   ("OK", [b"3 (FLAGS (\\Seen))",
                                           (b"1 (UID 7 BODY[] {4}", b"a\r\nb"), b")"]))]}]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "imap.txt"
            imap_recording.write_recording(path, "imap", batches)
            self.assertEqual(imap_recording.read_recording(path)["batches"], batches)


if __name__ == "__main__":
    unittest.main()
