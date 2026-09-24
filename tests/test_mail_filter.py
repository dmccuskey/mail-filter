import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mail_filter  # noqa: E402


def raw_message(to="", subject="", from_="sender@example.com", headers=()):
    extra = "".join(f"{name}: {value}\r\n" for name, value in headers)
    return (
        f"From: {from_}\r\n"
        f"To: {to}\r\n"
        f"{extra}"
        f"Subject: {subject}\r\n"
        "\r\n"
        "body\r\n"
    ).encode()


GENERIC_CAPABILITIES = b"IMAP4rev1 LITERAL+ SASL-IR LOGIN-REFERRALS ID ENABLE IDLE UIDPLUS MOVE"
GMAIL_CAPABILITIES = (
    b"IMAP4rev1 UNSELECT IDLE NAMESPACE QUOTA ID XLIST CHILDREN X-GM-EXT-1 "
    b"UIDPLUS COMPRESS=DEFLATE ENABLE MOVE CONDSTORE ESEARCH UTF8=ACCEPT"
)


# LIST data exactly as imaplib's list() returns it: one bytes line per mailbox,
# without the "* LIST" prefix. Trash lines are the ones the production servers
# reported; the rest are typical of each server.
GMAIL_LIST = [
    b'(\\HasNoChildren) "/" "INBOX"',
    b'(\\HasNoChildren) "/" "GitHub"',
    b'(\\HasChildren \\Noselect) "/" "[Gmail]"',
    b'(\\All \\HasNoChildren) "/" "[Gmail]/All Mail"',
    b'(\\Drafts \\HasNoChildren) "/" "[Gmail]/Drafts"',
    b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Sent Mail"',
    b'(\\HasNoChildren \\Junk) "/" "[Gmail]/Spam"',
    b'(\\Flagged \\HasNoChildren) "/" "[Gmail]/Starred"',
    b'(\\HasNoChildren \\Trash) "/" "[Gmail]/Trash"',
]
DOVECOT_LIST = [
    b'(\\HasChildren) "." INBOX',
    b'(\\HasNoChildren \\UnMarked) "." INBOX.Archive',
    b'(\\HasNoChildren \\UnMarked \\Sent) "." INBOX.Sent',
    b'(\\HasNoChildren \\UnMarked \\Trash) "." INBOX.Trash',
    b'(\\HasNoChildren \\UnMarked \\Junk) "." INBOX.spam',
]
# A server without special-use attributes, still with a folder named "Trash"
NO_ROLE_LIST = [
    b'(\\HasChildren) "." INBOX',
    b'(\\HasNoChildren) "." INBOX.Trash',
]


class FakeIMAP:
    """Records UID commands; fails loudly on any sequence-number command."""

    def __init__(self, messages, copy_status="OK", gmail=False,
                 capability_status="OK", failing_stores=(),
                 mailboxes=None, list_status="OK"):
        # messages: {uid (bytes): raw RFC 822 bytes}
        self.messages = messages
        self.copy_status = copy_status
        # imaplib returns capabilities as one space-separated line
        self.capability_response = (
            capability_status, [GMAIL_CAPABILITIES if gmail else GENERIC_CAPABILITIES]
        )
        # (item, flags) pairs whose UID STORE returns NO, e.g. ("-X-GM-LABELS", r"(\Inbox)")
        self.failing_stores = set(failing_stores)
        self.list_response = (list_status, NO_ROLE_LIST if mailboxes is None else mailboxes)
        self.calls = []

    def __call__(self, host):
        return self

    def login(self, user, password):
        return "OK", [b""]

    def select(self, mailbox, readonly=False):
        return "OK", [b"1"]

    def capability(self):
        return self.capability_response

    def list(self, directory='""', pattern="*"):
        self.calls.append(("LIST", directory, pattern))
        return self.list_response

    def uid(self, command, *args):
        self.calls.append(("UID", command) + args)
        if command == "SEARCH":
            return "OK", [b" ".join(self.messages)]
        if command == "FETCH":
            uid = args[0]
            return "OK", [(uid + b" (UID " + uid + b" BODY[] {n}", self.messages[uid]), b")"]
        if command == "COPY":
            return self.copy_status, [b""]
        if command == "STORE":
            status = "NO" if tuple(args[1:]) in self.failing_stores else "OK"
            return status, [b""]
        raise AssertionError(f"unexpected UID command {command}")

    def expunge(self):
        self.calls.append(("EXPUNGE",))
        return "OK", [b""]

    def logout(self):
        return "BYE", [b""]

    def _sequence_command(self, *args, **kwargs):
        raise AssertionError("sequence-number IMAP command used")

    search = fetch = store = copy = _sequence_command

    def message_calls(self):
        return [c for c in self.calls if c[0] == "UID" and c[1] in ("STORE", "COPY")]


ACCOUNT_CFG = {"imap_host": "imap.example.com", "username": "u", "password": "p"}
ACCOUNT = {"id": "test", **ACCOUNT_CFG}
FOLDERS = {"shop": "Shopping", "archive": "Archive"}
TRASH_FOLDERS = {**FOLDERS, "trash": "INBOX.Trash"}
GMAIL_FOLDERS = {"github": "GitHub", "trash": "[Gmail]/Trash"}

SEEN = ("+FLAGS", "(\\Seen)")
DELETED = ("+FLAGS", "(\\Deleted)")
REMOVE_INBOX = ("-X-GM-LABELS", r"(\Inbox)")


def run_account(fake, rules, folders=FOLDERS, dry_run=False):
    with mock.patch.object(mail_filter.imaplib, "IMAP4_SSL", fake), \
            mock.patch.object(mail_filter, "DRY_RUN", dry_run), \
            redirect_stdout(io.StringIO()) as out:
        mail_filter.process_account(ACCOUNT, folders, rules)
    return out.getvalue()


def rule(match, do, name="r"):
    return {"name": name, "match": match, "do": do}


class ProcessAccountUidTests(unittest.TestCase):

    def test_search_and_fetch_use_uids(self):
        fake = FakeIMAP({b"101": raw_message(to="x@example.com"),
                         b"205": raw_message(to="y@example.com")})
        run_account(fake, {"rules": []})

        self.assertEqual(fake.calls[0], ("UID", "SEARCH", "UNSEEN"))
        fetches = [c for c in fake.calls if c[1:2] == ("FETCH",)]
        self.assertEqual(fetches, [
            ("UID", "FETCH", b"101", "(BODY.PEEK[])"),
            ("UID", "FETCH", b"205", "(BODY.PEEK[])"),
        ])
        self.assertEqual(fake.message_calls(), [])

    def test_move_with_mark_read(self):
        fake = FakeIMAP({b"101": raw_message(to="shop-amazon@example.com")})
        run_account(fake, {"rules": [
            rule({"to_local_starts_with": ["shop-"]}, {"move": "shop", "mark_read": True}),
        ]})

        self.assertEqual(fake.message_calls(), [
            ("UID", "STORE", b"101", "+FLAGS", "(\\Seen)"),
            ("UID", "COPY", b"101", '"Shopping"'),
            ("UID", "STORE", b"101", "+FLAGS", "(\\Deleted)"),
        ])
        self.assertEqual(fake.calls[-1], ("EXPUNGE",))
        self.assertEqual(fake.calls.count(("EXPUNGE",)), 1)

    def test_move_without_mark_read(self):
        fake = FakeIMAP({b"7": raw_message(to="shop-a@example.com"),
                         b"9": raw_message(to="shop-b@example.com")})
        run_account(fake, {"rules": [
            rule({"to_local_starts_with": ["shop-"]}, {"move": "shop"}),
        ]})

        self.assertEqual(fake.message_calls(), [
            ("UID", "COPY", b"7", '"Shopping"'),
            ("UID", "STORE", b"7", "+FLAGS", "(\\Deleted)"),
            ("UID", "COPY", b"9", '"Shopping"'),
            ("UID", "STORE", b"9", "+FLAGS", "(\\Deleted)"),
        ])
        # EXPUNGE only after all messages are processed
        self.assertEqual(fake.calls[-1], ("EXPUNGE",))
        self.assertEqual(fake.calls.count(("EXPUNGE",)), 1)

    def test_failed_copy_does_not_delete_source(self):
        fake = FakeIMAP({b"101": raw_message(to="shop-a@example.com")}, copy_status="NO")
        run_account(fake, {"rules": [
            rule({"to_local_starts_with": ["shop-"]}, {"move": "shop"}),
        ]})

        self.assertEqual(fake.message_calls(), [("UID", "COPY", b"101", '"Shopping"')])

    def test_delete(self):
        fake = FakeIMAP({b"42": raw_message(subject="Spam offer")})
        run_account(fake, {"rules": [
            rule({"subject_contains": ["spam"]}, {"delete": True}),
        ]})

        self.assertEqual(fake.message_calls(), [
            ("UID", "STORE", b"42", "+FLAGS", "(\\Deleted)"),
        ])

    def test_delete_with_mark_read(self):
        fake = FakeIMAP({b"42": raw_message(subject="Spam offer")})
        run_account(fake, {"rules": [
            rule({"subject_contains": ["spam"]}, {"delete": True, "mark_read": True}),
        ]})

        self.assertEqual(fake.message_calls(), [
            ("UID", "STORE", b"42", "+FLAGS", "(\\Seen)"),
            ("UID", "STORE", b"42", "+FLAGS", "(\\Deleted)"),
        ])

    def test_catch_all(self):
        fake = FakeIMAP({b"300": raw_message(to="random@example.com")})
        run_account(fake, {
            "rules": [rule({"to_local_starts_with": ["shop-"]}, {"move": "shop"})],
            "catch_all": {"move": "archive"},
        })

        self.assertEqual(fake.message_calls(), [
            ("UID", "COPY", b"300", '"Archive"'),
            ("UID", "STORE", b"300", "+FLAGS", "(\\Deleted)"),
        ])

    def test_no_rule_and_no_catch_all(self):
        fake = FakeIMAP({b"300": raw_message(to="random@example.com")})
        run_account(fake, {"rules": [rule({"to_local_starts_with": ["shop-"]}, {"move": "shop"})]})

        self.assertEqual(fake.message_calls(), [])

    def test_rule_without_action(self):
        fake = FakeIMAP({b"300": raw_message(to="shop-a@example.com")})
        run_account(fake, {"rules": [rule({"to_local_starts_with": ["shop-"]}, {})]})

        self.assertEqual(fake.message_calls(), [])


class GenericTrashTests(unittest.TestCase):
    """trash on a non-Gmail server: same COPY + \\Deleted + EXPUNGE path as move."""

    def test_trash_copies_to_configured_trash_then_expunges(self):
        fake = FakeIMAP({b"42": raw_message(subject="Spam offer")})
        run_account(fake, {"rules": [rule({"subject_contains": ["spam"]}, {"trash": True})]},
                    folders=TRASH_FOLDERS)

        self.assertEqual(fake.message_calls(), [
            ("UID", "COPY", b"42", '"INBOX.Trash"'),
            ("UID", "STORE", b"42") + DELETED,
        ])
        self.assertEqual(fake.calls[-1], ("EXPUNGE",))
        self.assertEqual(fake.calls.count(("EXPUNGE",)), 1)

    def test_trash_with_mark_read(self):
        fake = FakeIMAP({b"42": raw_message(subject="Spam offer")})
        run_account(fake, {"rules": [rule({}, {"trash": True, "mark_read": True})]},
                    folders=TRASH_FOLDERS)

        self.assertEqual(fake.message_calls(), [
            ("UID", "STORE", b"42") + SEEN,
            ("UID", "COPY", b"42", '"INBOX.Trash"'),
            ("UID", "STORE", b"42") + DELETED,
        ])

    def test_trash_without_mapping_or_server_trash_leaves_message_untouched(self):
        fake = FakeIMAP({b"42": raw_message(subject="Spam offer")})
        run_account(fake, {"rules": [rule({}, {"trash": True, "mark_read": True})]})

        self.assertEqual(fake.message_calls(), [])
        self.assertNotIn(("EXPUNGE",), fake.calls)

    def test_trash_is_not_delete(self):
        # trash never marks the source deleted without a successful copy first
        fake = FakeIMAP({b"42": raw_message(subject="Spam offer")}, copy_status="NO")
        run_account(fake, {"rules": [rule({}, {"trash": True})]}, folders=TRASH_FOLDERS)

        self.assertEqual(fake.message_calls(), [("UID", "COPY", b"42", '"INBOX.Trash"')])
        self.assertNotIn(("EXPUNGE",), fake.calls)


class GenericSafetyTests(unittest.TestCase):

    def test_move_without_folder_mapping_leaves_message_untouched(self):
        fake = FakeIMAP({b"42": raw_message(to="shop-a@example.com")})
        run_account(fake, {"rules": [rule({}, {"move": "missing", "mark_read": True})]})

        self.assertEqual(fake.message_calls(), [])
        self.assertNotIn(("EXPUNGE",), fake.calls)

    def test_failed_deleted_flag_skips_expunge(self):
        fake = FakeIMAP({b"42": raw_message(to="shop-a@example.com")},
                        failing_stores=[DELETED])
        run_account(fake, {"rules": [rule({}, {"move": "shop"})]})

        self.assertEqual(fake.message_calls(), [
            ("UID", "COPY", b"42", '"Shopping"'),
            ("UID", "STORE", b"42") + DELETED,
        ])
        self.assertNotIn(("EXPUNGE",), fake.calls)

    def test_no_expunge_when_nothing_was_marked_deleted(self):
        fake = FakeIMAP({b"42": raw_message(to="random@example.com")})
        run_account(fake, {"rules": [rule({"to_local_is": ["me"]}, {"move": "shop"})]})

        self.assertNotIn(("EXPUNGE",), fake.calls)

    def test_delete_expunges_after_loop(self):
        fake = FakeIMAP({b"42": raw_message(subject="Spam offer")})
        run_account(fake, {"rules": [rule({}, {"delete": True})]})

        self.assertEqual(fake.message_calls(), [("UID", "STORE", b"42") + DELETED])
        self.assertEqual(fake.calls[-1], ("EXPUNGE",))

    def test_dry_run_trash_does_nothing(self):
        fake = FakeIMAP({b"42": raw_message(subject="Spam offer")})
        run_account(fake, {"rules": [rule({}, {"trash": True, "mark_read": True})]},
                    folders=TRASH_FOLDERS, dry_run=True)

        self.assertEqual(fake.message_calls(), [])
        self.assertNotIn(("EXPUNGE",), fake.calls)


class GmailTests(unittest.TestCase):
    """Gmail (X-GM-EXT-1): COPY adds the label, -X-GM-LABELS removes \\Inbox."""

    def all_stores(self, fake):
        return [c for c in fake.calls if c[:2] == ("UID", "STORE")]

    def assert_never_deleted(self, fake):
        for call in self.all_stores(fake):
            self.assertNotIn("\\Deleted", " ".join(str(a) for a in call[3:]))
        self.assertNotIn(("EXPUNGE",), fake.calls)

    def test_move_adds_label_and_removes_inbox_without_expunge(self):
        fake = FakeIMAP({b"42": raw_message(from_="noreply@github.com")}, gmail=True)
        run_account(fake, {"rules": [rule({"from_email_contains": "github.com"}, {"move": "github"})]},
                    folders=GMAIL_FOLDERS)

        self.assertEqual(fake.message_calls(), [
            ("UID", "COPY", b"42", '"GitHub"'),
            ("UID", "STORE", b"42") + REMOVE_INBOX,
        ])
        self.assert_never_deleted(fake)

    def test_move_with_mark_read(self):
        fake = FakeIMAP({b"42": raw_message()}, gmail=True)
        run_account(fake, {"rules": [rule({}, {"move": "github", "mark_read": True})]},
                    folders=GMAIL_FOLDERS)

        self.assertEqual(fake.message_calls(), [
            ("UID", "STORE", b"42") + SEEN,
            ("UID", "COPY", b"42", '"GitHub"'),
            ("UID", "STORE", b"42") + REMOVE_INBOX,
        ])
        self.assert_never_deleted(fake)

    def test_trash_copies_to_configured_trash_and_removes_inbox(self):
        fake = FakeIMAP({b"42": raw_message(subject="Spam offer")}, gmail=True)
        run_account(fake, {"rules": [rule({}, {"trash": True})]}, folders=GMAIL_FOLDERS)

        self.assertEqual(fake.message_calls(), [
            ("UID", "COPY", b"42", '"[Gmail]/Trash"'),
            ("UID", "STORE", b"42") + REMOVE_INBOX,
        ])
        self.assert_never_deleted(fake)

    def test_trash_without_mapping_or_server_trash_leaves_message_untouched(self):
        fake = FakeIMAP({b"42": raw_message()}, gmail=True)
        run_account(fake, {"rules": [rule({}, {"trash": True})]}, folders={"github": "GitHub"})

        self.assertEqual(fake.message_calls(), [])
        self.assertNotIn(("EXPUNGE",), fake.calls)

    def test_copy_failure_keeps_inbox_label(self):
        fake = FakeIMAP({b"42": raw_message()}, gmail=True, copy_status="NO")
        run_account(fake, {"rules": [rule({}, {"move": "github"})]}, folders=GMAIL_FOLDERS)

        self.assertEqual(fake.message_calls(), [("UID", "COPY", b"42", '"GitHub"')])
        self.assert_never_deleted(fake)

    def test_label_removal_failure_never_falls_back_to_deleted(self):
        fake = FakeIMAP({b"42": raw_message(), b"43": raw_message()}, gmail=True,
                        failing_stores=[REMOVE_INBOX])
        run_account(fake, {"rules": [rule({}, {"trash": True})]}, folders=GMAIL_FOLDERS)

        self.assertEqual(fake.message_calls(), [
            ("UID", "COPY", b"42", '"[Gmail]/Trash"'),
            ("UID", "STORE", b"42") + REMOVE_INBOX,
            ("UID", "COPY", b"43", '"[Gmail]/Trash"'),
            ("UID", "STORE", b"43") + REMOVE_INBOX,
        ])
        self.assert_never_deleted(fake)

    def test_delete_keeps_deleted_and_expunge_semantics(self):
        fake = FakeIMAP({b"42": raw_message()}, gmail=True)
        run_account(fake, {"rules": [rule({}, {"delete": True})]}, folders=GMAIL_FOLDERS)

        self.assertEqual(fake.message_calls(), [("UID", "STORE", b"42") + DELETED])
        self.assertEqual(fake.calls[-1], ("EXPUNGE",))

    def test_mixed_batch_expunges_once_and_never_deletes_moved_message(self):
        fake = FakeIMAP({b"7": raw_message(from_="noreply@github.com"),
                         b"9": raw_message(subject="Spam offer")}, gmail=True)
        run_account(fake, {"rules": [
            rule({"from_email_contains": "github.com"}, {"move": "github"}),
            rule({"subject_contains": "spam"}, {"delete": True}),
        ]}, folders=GMAIL_FOLDERS)

        self.assertEqual(fake.message_calls(), [
            ("UID", "COPY", b"7", '"GitHub"'),
            ("UID", "STORE", b"7") + REMOVE_INBOX,
            ("UID", "STORE", b"9") + DELETED,
        ])
        self.assertEqual(fake.calls[-1], ("EXPUNGE",))
        self.assertEqual(fake.calls.count(("EXPUNGE",)), 1)


class FindAdvertisedTrashTests(unittest.TestCase):
    """Parsing of imaplib LIST data for the RFC 6154 \\Trash attribute."""

    def find(self, entries, status="OK"):
        imap = mock.Mock()
        imap.list = mock.Mock(return_value=(status, entries))
        return mail_filter.find_advertised_trash(imap)

    def test_gmail_quoted_name(self):
        self.assertEqual(self.find(GMAIL_LIST), ("[Gmail]/Trash", None))

    def test_dovecot_unquoted_name(self):
        self.assertEqual(self.find(DOVECOT_LIST), ("INBOX.Trash", None))

    def test_name_sent_as_literal(self):
        entries = [b'(\\HasChildren) "." INBOX',
                   (b'(\\HasNoChildren \\Trash) "/" {9}', b"Corbeille")]
        self.assertEqual(self.find(entries), ("Corbeille", None))

    def test_attribute_is_case_insensitive(self):
        self.assertEqual(self.find([b'(\\trash) "/" Deleted']), ("Deleted", None))

    def test_folder_named_trash_without_attribute_is_not_used(self):
        mailbox, problem = self.find(NO_ROLE_LIST)
        self.assertIsNone(mailbox)
        self.assertIn("no \\Trash", problem)

    def test_similar_attribute_is_not_trash(self):
        self.assertEqual(self.find([b'(\\TrashCan) "/" Bin'])[0], None)

    def test_empty_list(self):
        # imaplib returns [None] when the server sends no LIST lines
        self.assertIsNone(self.find([None])[0])

    def test_multiple_trash_mailboxes_are_ambiguous(self):
        mailbox, problem = self.find(DOVECOT_LIST + [b'(\\Trash) "." INBOX.Bin'])
        self.assertIsNone(mailbox)
        self.assertIn("2", problem)

    def test_list_failure(self):
        self.assertIsNone(self.find([b"denied"], status="NO")[0])
        imap = mock.Mock()
        imap.list = mock.Mock(side_effect=mail_filter.imaplib.IMAP4.error("boom"))
        self.assertIsNone(mail_filter.find_advertised_trash(imap)[0])

    def test_names_that_cannot_be_quoted_safely_are_refused(self):
        for name in (b'Tr"ash', b"Tr\\ash"):
            with self.subTest(name=name):
                entries = [(b'(\\Trash) "/" {6}', name)]
                self.assertIsNone(self.find(entries)[0])

    def test_non_ascii_name_is_refused(self):
        entries = [(b'(\\Trash) "/" {9}', "Корзина".encode())]
        self.assertIsNone(self.find(entries)[0])


class TrashResolutionTests(unittest.TestCase):
    """trash: true resolves config "trash" first, then the server's \\Trash."""

    def list_calls(self, fake):
        return [c for c in fake.calls if c[0] == "LIST"]

    def test_generic_uses_server_trash_without_mapping(self):
        fake = FakeIMAP({b"42": raw_message(), b"43": raw_message()}, mailboxes=DOVECOT_LIST)
        run_account(fake, {"rules": [rule({}, {"trash": True})]})

        self.assertEqual(fake.message_calls(), [
            ("UID", "COPY", b"42", '"INBOX.Trash"'),
            ("UID", "STORE", b"42") + DELETED,
            ("UID", "COPY", b"43", '"INBOX.Trash"'),
            ("UID", "STORE", b"43") + DELETED,
        ])
        self.assertEqual(fake.calls[-1], ("EXPUNGE",))
        # resolved once per run, not once per message
        self.assertEqual(len(self.list_calls(fake)), 1)

    def test_gmail_uses_server_trash_without_mapping(self):
        fake = FakeIMAP({b"42": raw_message()}, gmail=True, mailboxes=GMAIL_LIST)
        run_account(fake, {"rules": [rule({}, {"trash": True})]}, folders={"github": "GitHub"})

        self.assertEqual(fake.message_calls(), [
            ("UID", "COPY", b"42", '"[Gmail]/Trash"'),
            ("UID", "STORE", b"42") + REMOVE_INBOX,
        ])
        self.assertNotIn(("EXPUNGE",), fake.calls)

    def test_mapping_overrides_server_trash_and_skips_list(self):
        fake = FakeIMAP({b"42": raw_message()}, mailboxes=DOVECOT_LIST)
        run_account(fake, {"rules": [rule({}, {"trash": True})]},
                    folders={**FOLDERS, "trash": "INBOX.Deleted Items"})

        self.assertEqual(fake.message_calls()[0], ("UID", "COPY", b"42", '"INBOX.Deleted Items"'))
        self.assertEqual(self.list_calls(fake), [])

    def test_no_list_when_no_trash_rule_fires(self):
        fake = FakeIMAP({b"42": raw_message(), b"43": raw_message(subject="Spam")},
                        mailboxes=DOVECOT_LIST)
        run_account(fake, {"rules": [
            rule({"subject_contains": "spam"}, {"trash": True}),
            rule({}, {"move": "shop"}),
        ]})
        self.assertEqual(len(self.list_calls(fake)), 1)

        fake = FakeIMAP({b"42": raw_message()}, mailboxes=DOVECOT_LIST)
        run_account(fake, {"rules": [rule({}, {"move": "shop"})]})
        self.assertEqual(self.list_calls(fake), [])

    def test_list_failure_skips_trash_safely(self):
        fake = FakeIMAP({b"42": raw_message(subject="Hi")}, mailboxes=[b"denied"],
                        list_status="NO")
        output = run_account(fake, {"rules": [rule({}, {"trash": True, "mark_read": True},
                                                   name="Rule A")]})

        self.assertEqual(fake.message_calls(), [])
        self.assertNotIn(("EXPUNGE",), fake.calls)
        self.assertIn("MATCHED #42 RULE='Rule A' SUBJECT='Hi' SKIPPED: no Trash mailbox: LIST failed (status=NO)\n", output)

    def test_ambiguous_server_trash_skips_safely(self):
        fake = FakeIMAP({b"42": raw_message()}, gmail=True,
                        mailboxes=GMAIL_LIST + [b'(\\Trash) "/" "Old Trash"'])
        run_account(fake, {"rules": [rule({}, {"trash": True})]}, folders={"github": "GitHub"})

        self.assertEqual(fake.message_calls(), [])
        self.assertNotIn(("EXPUNGE",), fake.calls)

    def test_dry_run_resolves_but_does_not_modify(self):
        fake = FakeIMAP({b"42": raw_message()}, mailboxes=DOVECOT_LIST)
        output = run_account(fake, {"rules": [rule({}, {"trash": True})]}, dry_run=True)

        self.assertEqual(fake.message_calls(), [])
        self.assertIn("→ Trash (INBOX.Trash) [DRY_RUN]", output)


class ProviderUnknownTests(unittest.TestCase):
    """A failed capability query must not be treated as 'generic IMAP'."""

    def test_move_and_trash_are_skipped(self):
        for do in ({"move": "shop", "mark_read": True}, {"trash": True, "mark_read": True}):
            with self.subTest(do=do):
                fake = FakeIMAP({b"42": raw_message()}, capability_status="NO")
                run_account(fake, {"rules": [rule({}, do)]}, folders=TRASH_FOLDERS)

                self.assertEqual(fake.message_calls(), [])
                self.assertNotIn(("EXPUNGE",), fake.calls)

    def test_capability_exception_skips_move(self):
        fake = FakeIMAP({b"42": raw_message()})
        fake.capability = mock.Mock(side_effect=mail_filter.imaplib.IMAP4.error("boom"))
        run_account(fake, {"rules": [rule({}, {"move": "shop"})]})

        self.assertEqual(fake.message_calls(), [])
        self.assertNotIn(("EXPUNGE",), fake.calls)

    def test_delete_is_unaffected(self):
        fake = FakeIMAP({b"42": raw_message()}, capability_status="NO")
        run_account(fake, {"rules": [rule({}, {"delete": True})]})

        self.assertEqual(fake.message_calls(), [("UID", "STORE", b"42") + DELETED])
        self.assertEqual(fake.calls[-1], ("EXPUNGE",))


class DetectGmailTests(unittest.TestCase):

    def detect(self, response=None, error=None):
        imap = mock.Mock()
        imap.capability = mock.Mock(return_value=response, side_effect=error)
        return mail_filter.detect_gmail(imap)

    def test_gmail_capability_line(self):
        self.assertIs(self.detect(("OK", [GMAIL_CAPABILITIES])), True)

    def test_generic_capability_line(self):
        self.assertIs(self.detect(("OK", [GENERIC_CAPABILITIES])), False)

    def test_token_must_match_exactly(self):
        self.assertIs(self.detect(("OK", [b"IMAP4rev1 X-GM-EXT-10"])), False)

    def test_failures_are_unknown_not_generic(self):
        self.assertIsNone(self.detect(("NO", [b"denied"])))
        self.assertIsNone(self.detect(("OK", [])))
        self.assertIsNone(self.detect(("OK", [None])))
        self.assertIsNone(self.detect(error=mail_filter.imaplib.IMAP4.error("boom")))
        self.assertIsNone(self.detect(error=OSError("reset")))


def message_lines(output):
    """Per-message log lines with the timestamp removed."""
    lines = [line.split("] ", 1)[1] for line in output.splitlines()]
    return [line for line in lines if " MATCHED #" in line or " NO_RULE #" in line]


class LogFormatTests(unittest.TestCase):
    """
    One line per message, written after the outcome is known
    (format spec: docs/operations.md).
    """

    def line_for(self, do, folders=FOLDERS, dry_run=False, **fake_kwargs):
        fake = FakeIMAP({b"42": raw_message(subject="Hi")}, **fake_kwargs)
        output = run_account(fake, {"rules": [rule({}, do, name="Rule A")]},
                             folders=folders, dry_run=dry_run)
        lines = message_lines(output)
        self.assertEqual(len(lines), 1, output)
        return lines[0]

    def test_exact_lines_for_each_outcome(self):
        prefix = "[test] MATCHED #42 RULE='Rule A' SUBJECT='Hi' "
        cases = [
            # (do, folders, FakeIMAP kwargs, expected outcome)
            ({"move": "shop"}, FOLDERS, {}, "→ Shopping"),
            ({"move": "shop", "mark_read": True}, FOLDERS, {}, "→ Shopping (mark_read)"),
            ({"trash": True}, TRASH_FOLDERS, {}, "→ Trash (INBOX.Trash)"),
            ({"trash": True}, FOLDERS, {"mailboxes": DOVECOT_LIST}, "→ Trash (INBOX.Trash)"),
            ({"move": "github"}, GMAIL_FOLDERS, {"gmail": True}, "→ GitHub"),
            ({"trash": True}, GMAIL_FOLDERS, {"gmail": True}, "→ Trash ([Gmail]/Trash)"),
            ({"delete": True}, FOLDERS, {}, "DELETED"),
            ({"delete": True, "mark_read": True}, FOLDERS, {}, "DELETED (mark_read)"),
            ({"mark_read": True}, FOLDERS, {}, "MARKED_READ"),
            ({}, FOLDERS, {}, "NO_ACTION"),
            ({"move": "missing"}, FOLDERS, {},
             "SKIPPED: no folder mapping for key 'missing'"),
            ({"trash": True}, FOLDERS, {},
             "SKIPPED: no Trash mailbox: server advertises no \\Trash mailbox"),
            ({"move": "shop"}, FOLDERS, {"capability_status": "NO"},
             "SKIPPED: provider unknown (capability query failed)"),
            ({"move": "shop"}, FOLDERS, {"copy_status": "NO"},
             "FAILED: copy to 'Shopping' refused (status=NO); left in INBOX"),
            ({"move": "shop", "mark_read": True}, FOLDERS, {"copy_status": "NO"},
             "FAILED: copy to 'Shopping' refused (status=NO); left in INBOX (marked read)"),
            ({"move": "shop"}, FOLDERS, {"failing_stores": [DELETED]},
             "FAILED: \\Deleted not set (status=NO); copied to 'Shopping', still in INBOX"),
            ({"move": "github", "mark_read": True}, GMAIL_FOLDERS,
             {"gmail": True, "failing_stores": [REMOVE_INBOX]},
             "FAILED: \\Inbox label not removed (status=NO); "
             "copied to 'GitHub', still in INBOX (marked read)"),
            ({"delete": True}, FOLDERS, {"failing_stores": [DELETED]},
             "FAILED: \\Deleted not set (status=NO); left in INBOX"),
            ({"mark_read": True}, FOLDERS, {"failing_stores": [SEEN]},
             "FAILED: \\Seen not set (status=NO); left in INBOX"),
            ({"move": "shop", "mark_read": True}, FOLDERS, {"failing_stores": [SEEN]},
             "→ Shopping (mark_read FAILED: status=NO)"),
        ]
        for do, folders, fake_kwargs, outcome in cases:
            with self.subTest(do=do, fake=fake_kwargs):
                self.assertEqual(self.line_for(do, folders, **fake_kwargs), prefix + outcome)

    def test_no_rule_line(self):
        fake = FakeIMAP({b"42": raw_message(to="random@example.com", subject="Hi")})
        output = run_account(fake, {"rules": [rule({"to_local_is": ["me"]}, {"move": "shop"})]})
        self.assertEqual(message_lines(output), ["[test] NO_RULE #42 SUBJECT='Hi'"])
        self.assertNotIn("MATCHED", output)

    def test_dry_run_tag(self):
        self.assertEqual(self.line_for({"move": "shop", "mark_read": True}, dry_run=True),
                         "[test] MATCHED #42 RULE='Rule A' SUBJECT='Hi' → Shopping (mark_read) [DRY_RUN]")
        self.assertEqual(self.line_for({"move": "missing"}, dry_run=True),
                         "[test] MATCHED #42 RULE='Rule A' SUBJECT='Hi' "
                         "SKIPPED: no folder mapping for key 'missing' [DRY_RUN]")
        fake = FakeIMAP({b"42": raw_message(to="random@example.com", subject="Hi")})
        output = run_account(fake, {"rules": []}, dry_run=True)
        self.assertEqual(message_lines(output), ["[test] NO_RULE #42 SUBJECT='Hi' [DRY_RUN]"])

    def test_one_line_per_message_and_no_other_matched_lines(self):
        fake = FakeIMAP({b"7": raw_message(subject="a"), b"8": raw_message(subject="b"),
                         b"9": raw_message(to="random@example.com", subject="c")},
                        copy_status="NO")
        output = run_account(fake, {"rules": [
            rule({"subject_contains": "a"}, {"move": "shop"}, name="Move"),
            rule({"subject_contains": "b"}, {"delete": True}, name="Del"),
        ]})
        self.assertEqual(message_lines(output), [
            "[test] MATCHED #7 RULE='Move' SUBJECT='a' "
            "FAILED: copy to 'Shopping' refused (status=NO); left in INBOX",
            "[test] MATCHED #8 RULE='Del' SUBJECT='b' DELETED",
            "[test] NO_RULE #9 SUBJECT='c'",
        ])
        # the old two-line failure format (a MATCHED line plus a separate ERROR line) is gone
        self.assertNotIn("ERROR", output)
        self.assertEqual(output.count("MATCHED"), 2)

    def test_uids_are_logged_as_hash_numbers_not_bytes(self):
        fake = FakeIMAP({b"16186": raw_message()})
        output = run_account(fake, {"rules": [rule({}, {"move": "shop"})]})
        self.assertIn(" MATCHED #16186 ", output)
        self.assertNotIn("b'", output)

    def test_rule_name_is_never_bracketed(self):
        fake = FakeIMAP({b"42": raw_message()})
        output = run_account(fake, {"rules": [
            rule({}, {"move": "shop", "delete": True}, name="odd, error"),
        ]})
        self.assertNotIn("[odd, error]", output)
        self.assertIn("Warning: RULE='odd, error' has move with trash/delete; using move", output)


class MarkReadOnlyTests(unittest.TestCase):
    """mark_read with no move/trash/delete sets \\Seen and leaves the message in INBOX."""

    def test_mark_read_only_sets_seen(self):
        for gmail in (False, True):
            with self.subTest(gmail=gmail):
                fake = FakeIMAP({b"42": raw_message()}, gmail=gmail)
                run_account(fake, {"rules": [rule({}, {"mark_read": True})]})

                self.assertEqual(fake.message_calls(), [("UID", "STORE", b"42") + SEEN])
                self.assertNotIn(("EXPUNGE",), fake.calls)

    def test_catch_all_mark_read_only(self):
        fake = FakeIMAP({b"42": raw_message(to="random@example.com")})
        run_account(fake, {"rules": [rule({"to_local_is": ["me"]}, {"move": "shop"})],
                           "catch_all": {"mark_read": True}})

        self.assertEqual(fake.message_calls(), [("UID", "STORE", b"42") + SEEN])

    def test_mark_read_false_does_nothing(self):
        fake = FakeIMAP({b"42": raw_message()})
        run_account(fake, {"rules": [rule({}, {"mark_read": False})]})

        self.assertEqual(fake.message_calls(), [])

    def test_mark_read_only_works_when_provider_unknown(self):
        # \\Seen is not provider-specific, so a failed capability query does not block it
        fake = FakeIMAP({b"42": raw_message()}, capability_status="NO")
        run_account(fake, {"rules": [rule({}, {"mark_read": True})]})

        self.assertEqual(fake.message_calls(), [("UID", "STORE", b"42") + SEEN])

    def test_failed_store_is_logged(self):
        fake = FakeIMAP({b"42": raw_message()}, failing_stores=[SEEN])
        output = run_account(fake, {"rules": [rule({}, {"mark_read": True})]})

        self.assertEqual(fake.message_calls(), [("UID", "STORE", b"42") + SEEN])
        self.assertIn("SUBJECT='' FAILED: \\Seen not set (status=NO); left in INBOX\n", output)

    def test_dry_run_does_nothing(self):
        fake = FakeIMAP({b"42": raw_message()})
        output = run_account(fake, {"rules": [rule({}, {"mark_read": True})]}, dry_run=True)

        self.assertEqual(fake.message_calls(), [])
        self.assertIn(" MARKED_READ [DRY_RUN]\n", output)


class ChooseRuleTests(unittest.TestCase):

    def choose(self, rules_cfg, addresses=(), subject="", from_addr="", from_name=""):
        with redirect_stdout(io.StringIO()):
            return mail_filter.choose_rule(list(addresses), subject, from_addr, from_name, rules_cfg)

    def test_first_match_wins(self):
        cfg = {"rules": [
            rule({"to_local_is": ["shop-amazon"]}, {"move": "amazon"}, name="specific"),
            rule({"to_local_starts_with": ["shop-"]}, {"move": "shop"}, name="broad"),
        ]}
        self.assertEqual(self.choose(cfg, ["shop-amazon@example.com"])["name"], "specific")
        self.assertEqual(self.choose(cfg, ["shop-ebay@example.com"])["name"], "broad")

    def test_every_matcher_hit_and_miss(self):
        # (field, value, hit, miss): hit/miss are choose() keyword arguments
        to = lambda *a: {"addresses": list(a)}
        cases = [
            ("to_is", "Shop-Amazon@Example.com", to("shop-amazon@example.com"), to("shop-amazon@example.org")),
            ("to_contains", "amazon@exa", to("shop-amazon@example.com"), to("shop-ebay@example.com")),
            ("to_starts_with", "shop-", to("shop-amazon@example.com"), to("my-shop-amazon@example.com")),
            ("to_ends_with", "@example.com", to("me@example.com"), to("me@example.com.au")),
            ("to_local_is", "shop-amazon", to("shop-amazon@example.com"), to("shop-amazon2@example.com")),
            ("to_local_contains", "amaz", to("shop-amazon@example.com"), to("shop@amazon.com")),
            ("to_local_starts_with", "shop-", to("shop-amazon@example.com"), to("my-shop@example.com")),
            ("to_local_ends_with", "-receipts", to("aws-receipts@example.com"), to("me@example-receipts")),
            ("from_email_is", "noreply@github.com", {"from_addr": "noreply@github.com"}, {"from_addr": "noreply@github.com.evil"}),
            ("from_email_contains", "github.com", {"from_addr": "n@github.com"}, {"from_addr": "hub@tig.com"}),
            ("from_email_starts_with", "noreply@", {"from_addr": "noreply@github.com"}, {"from_addr": "x-noreply@github.com"}),
            ("from_email_ends_with", "@github.com", {"from_addr": "n@github.com"}, {"from_addr": "n@github.community"}),
            ("from_name_is", "GitHub", {"from_name": "github"}, {"from_name": "github bot"}),
            ("from_name_contains", "github", {"from_name": "the github bot"}, {"from_name": "big hut"}),
            ("from_name_starts_with", "github", {"from_name": "github bot"}, {"from_name": "the github bot"}),
            ("from_name_ends_with", "bot", {"from_name": "github bot"}, {"from_name": "bot army"}),
            ("subject_is", "Hello", {"subject": "hello"}, {"subject": "hello there"}),
            ("subject_contains", "invoice", {"subject": "Your Invoice #1"}, {"subject": "voice note"}),
            ("subject_starts_with", "re:", {"subject": "RE: lunch"}, {"subject": "Fwd: RE: lunch"}),
            ("subject_ends_with", "[urgent]", {"subject": "Server down [URGENT]"}, {"subject": "[urgent] server"}),
        ]
        self.assertEqual({c[0] for c in cases}, set(mail_filter.MATCHERS))
        for field, value, hit, miss in cases:
            for form in (value, [value]):
                with self.subTest(field=field, form=form):
                    cfg = {"rules": [rule({field: form}, {"move": "x"})]}
                    self.assertIsNotNone(self.choose(cfg, **hit))
                    self.assertIsNone(self.choose(cfg, **miss))

    def test_matching_is_case_insensitive(self):
        cfg = {"rules": [rule({"subject_contains": "INVOICE"}, {"move": "x"})]}
        self.assertIsNotNone(self.choose(cfg, subject="your invoice"))
        cfg = {"rules": [rule({"to_is": "me@example.com"}, {"move": "x"})]}
        self.assertIsNotNone(self.choose(cfg, ["ME@EXAMPLE.COM"]))

    def test_to_is_needs_full_address(self):
        cfg = {"rules": [rule({"to_is": "shop-amazon"}, {"move": "x"})]}
        self.assertIsNone(self.choose(cfg, ["shop-amazon@example.com"]))

    def test_to_local_ignores_domain(self):
        cfg = {"rules": [rule({"to_local_is": "shop-amazon"}, {"move": "x"})]}
        self.assertIsNotNone(self.choose(cfg, ["shop-amazon@example.com"]))
        self.assertIsNotNone(self.choose(cfg, ["shop-amazon@other.org"]))
        cfg = {"rules": [rule({"to_local_contains": "example"}, {"move": "x"})]}
        self.assertIsNone(self.choose(cfg, ["me@example.com"]))

    def test_any_to_address_can_match(self):
        for field, value in (("to_is", "b@example.com"), ("to_local_is", "b")):
            with self.subTest(field=field):
                cfg = {"rules": [rule({field: value}, {"move": "x"})]}
                self.assertIsNotNone(self.choose(cfg, ["a@example.com", "b@example.com"]))
                self.assertIsNone(self.choose(cfg, ["a@example.com", "c@example.com"]))

    def test_list_values_are_ored(self):
        cfg = {"rules": [rule({"subject_contains": ["receipt", "invoice"]}, {"move": "x"})]}
        self.assertIsNotNone(self.choose(cfg, subject="Your receipt"))
        self.assertIsNotNone(self.choose(cfg, subject="Your invoice"))
        self.assertIsNone(self.choose(cfg, subject="Your order"))

    def test_fields_are_anded(self):
        cfg = {"rules": [rule({"to_local_is": "shop-amazon", "subject_contains": "receipt"},
                              {"move": "x"})]}
        self.assertIsNotNone(self.choose(cfg, ["shop-amazon@example.com"], subject="Receipt"))
        self.assertIsNone(self.choose(cfg, ["shop-amazon@example.com"], subject="Deals"))
        self.assertIsNone(self.choose(cfg, ["other@example.com"], subject="Receipt"))

    def test_string_value_is_one_token_not_characters(self):
        # Regression: a bare string was iterated character by character,
        # so "rachellehmannhaupt" matched almost any sender.
        cfg = {"rules": [rule({"from_email_contains": "rachellehmannhaupt"}, {"move": "x"})]}
        self.assertIsNotNone(self.choose(cfg, from_addr="rachellehmannhaupt@example.com"))
        self.assertIsNone(self.choose(cfg, from_addr="verizon-notifications@verizon.com"))

    def test_unknown_field_raises(self):
        for field in ("from_contains", "to", "to_prefix", "subject_equals", "subjet_contains"):
            with self.subTest(field=field):
                cfg = {"rules": [rule({field: "x"}, {"move": "x"}, name="Typo rule")]}
                with self.assertRaises(mail_filter.RuleConfigError) as ctx:
                    self.choose(cfg, subject="x")
                self.assertIn("'Typo rule'", str(ctx.exception))
                self.assertIn(f"'{field}'", str(ctx.exception))

    def test_non_matching(self):
        cfg = {"rules": [rule({"subject_is": ["Hello"]}, {"move": "x"})]}
        self.assertIsNone(self.choose(cfg, subject="Hello there"))

    def test_move_beats_delete(self):
        cfg = {"rules": [rule({}, {"move": "x", "delete": True, "mark_read": True})]}
        result = self.choose(cfg)
        self.assertEqual(result["move"], "x")
        self.assertFalse(result["trash"])
        self.assertFalse(result["delete"])
        self.assertTrue(result["mark_read"])

    def test_move_beats_trash(self):
        result = self.choose({"rules": [rule({}, {"move": "x", "trash": True})]})
        self.assertEqual(result["move"], "x")
        self.assertFalse(result["trash"])

    def test_trash_beats_delete(self):
        result = self.choose({"rules": [rule({}, {"trash": True, "delete": True})]})
        self.assertTrue(result["trash"])
        self.assertFalse(result["delete"])

    def test_trash_keeps_mark_read(self):
        result = self.choose({"rules": [rule({}, {"trash": True, "mark_read": True})]})
        self.assertEqual(result, {"name": "r", "move": None, "trash": True,
                                  "delete": False, "mark_read": True})

    def test_catch_all_trash(self):
        cfg = {"rules": [], "catch_all": {"trash": True, "delete": True}}
        result = self.choose(cfg)
        self.assertEqual(result["name"], "<catch_all>")
        self.assertTrue(result["trash"])
        self.assertFalse(result["delete"])

    def test_catch_all_fallback(self):
        cfg = {"rules": [rule({"to_local_is": ["me"]}, {"move": "x"})],
               "catch_all": {"move": "archive", "mark_read": False}}
        self.assertEqual(self.choose(cfg, ["other@example.com"]), {
            "name": "<catch_all>", "move": "archive", "trash": False,
            "delete": False, "mark_read": False,
        })


class ToHeaderOnlyTests(unittest.TestCase):
    """to_* and to_local_* read the To header only, through process_account."""

    def moved(self, message, match):
        fake = FakeIMAP({b"7": message})
        run_account(fake, {"rules": [rule(match, {"move": "shop"})]})
        return any(c[1] == "COPY" for c in fake.message_calls())

    def test_to_header_matches(self):
        message = raw_message(to="Other <other@example.com>, Me <me@example.com>")
        self.assertTrue(self.moved(message, {"to_is": "me@example.com"}))
        self.assertTrue(self.moved(message, {"to_local_is": "me"}))

    def test_other_recipient_headers_are_ignored(self):
        for header in ("Cc", "Delivered-To", "X-Original-To"):
            message = raw_message(to="other@example.com", headers=[(header, "me@example.com")])
            for match in ({"to_is": "me@example.com"}, {"to_local_is": "me"}):
                with self.subTest(header=header, match=match):
                    self.assertFalse(self.moved(message, match))


class ValidateRulesTests(unittest.TestCase):

    def test_valid_rules_pass(self):
        cfg = {"rules": [rule({m: "x" for m in mail_filter.MATCHERS}, {"move": "x"}),
                         rule({}, {"delete": True}), {"name": "no match key", "do": {}}]}
        self.assertEqual(mail_filter.validate_rules(cfg), [])

    def test_reports_every_unknown_field(self):
        cfg = {"rules": [rule({"subject_contains": "a"}, {"move": "x"}, name="Fine"),
                         rule({"from_contains": "github.com", "to": "me"}, {"move": "x"}, name="GitHub")]}
        self.assertEqual(mail_filter.validate_rules(cfg), [
            "rule #2 'GitHub' uses unknown match field 'from_contains'",
            "rule #2 'GitHub' uses unknown match field 'to'",
        ])

    def test_main_rejects_unknown_field_before_connecting(self):
        configs = {
            "accounts.local.json5": {"test": ACCOUNT_CFG},
            "folders.local.json5": {"test": FOLDERS},
            "rules.local.json5": {"test": {"rules": [rule({"to_prefix": "shop-"}, {"move": "shop"})]}},
        }
        connect = mock.Mock(side_effect=AssertionError("connected despite bad rules"))
        with mock.patch.object(mail_filter, "load_json", lambda path: configs[Path(path).name]), \
                mock.patch.object(mail_filter.imaplib, "IMAP4_SSL", connect), \
                redirect_stdout(io.StringIO()) as out:
            with self.assertRaises(SystemExit) as ctx:
                mail_filter.main()
        connect.assert_not_called()
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("] [test] ERROR: rule #1 'r' uses unknown match field 'to_prefix'\n",
                      out.getvalue())
        self.assertIn("ERROR: 1 unknown match field(s) in rules.local.json5; no mail processed",
                      out.getvalue())
        self.assertNotIn("Traceback", out.getvalue())


class ConfigFormatTests(unittest.TestCase):
    """Every config file is keyed by account ID; the old wrappers are rejected."""

    RULES = {"test": {"rules": [rule({"to_local_is": ["me"]}, {"move": "shop"})]}}

    def run_main(self, accounts, folders):
        configs = {
            "accounts.local.json5": accounts,
            "folders.local.json5": folders,
            "rules.local.json5": self.RULES,
        }
        process = mock.Mock()
        with mock.patch.object(mail_filter, "load_json", lambda path: configs[Path(path).name]), \
                mock.patch.object(mail_filter, "process_account", process), \
                redirect_stdout(io.StringIO()) as out:
            try:
                mail_filter.main()
                code = None
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), process

    def test_valid_config_has_no_problems(self):
        self.assertEqual(
            mail_filter.validate_config_format({"test": ACCOUNT_CFG}, {"test": FOLDERS}), [])

    def test_accounts_processed_in_file_order_with_id(self):
        accounts = {"b": ACCOUNT_CFG, "a": ACCOUNT_CFG}
        folders = {"b": FOLDERS, "a": FOLDERS}
        self.RULES = {"b": self.RULES["test"], "a": self.RULES["test"]}
        code, _, process = self.run_main(accounts, folders)
        self.assertIsNone(code)
        self.assertEqual([c.args[0] for c in process.call_args_list],
                         [{"id": "b", **ACCOUNT_CFG}, {"id": "a", **ACCOUNT_CFG}])

    def test_old_accounts_list_rejected(self):
        code, out, process = self.run_main({"accounts": [ACCOUNT]}, {"test": FOLDERS})
        self.assertEqual(code, 1)
        process.assert_not_called()
        self.assertIn('ERROR: accounts.local.json5 uses the old format (top-level "accounts" list)',
                      out)
        self.assertIn("ERROR: configuration format problem(s); no mail processed", out)
        self.assertNotIn("Traceback", out)

    def test_old_folders_wrapper_rejected(self):
        code, out, process = self.run_main({"test": ACCOUNT_CFG}, {"folders": {"test": FOLDERS}})
        self.assertEqual(code, 1)
        process.assert_not_called()
        self.assertIn('ERROR: folders.local.json5 uses the old format (top-level "folders" wrapper)',
                      out)

    def test_both_old_formats_reported_together(self):
        problems = mail_filter.validate_config_format(
            {"accounts": [ACCOUNT]}, {"folders": {"test": FOLDERS}})
        self.assertEqual(len(problems), 2)
        self.assertTrue(problems[0].startswith("accounts.local.json5 uses the old format"))
        self.assertTrue(problems[1].startswith("folders.local.json5 uses the old format"))

    def test_account_named_folders_is_allowed(self):
        self.assertEqual(
            mail_filter.validate_config_format({"folders": ACCOUNT_CFG}, {"folders": FOLDERS}), [])

    def test_non_object_account_rejected(self):
        self.assertEqual(
            mail_filter.validate_config_format({"test": "imap.example.com"}, {}),
            ["account 'test' in accounts.local.json5 must be an object"])


if __name__ == "__main__":
    unittest.main()
