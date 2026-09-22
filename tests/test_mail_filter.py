import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mail_filter  # noqa: E402


def raw_message(to="", subject="", from_="sender@example.com"):
    return (
        f"From: {from_}\r\n"
        f"To: {to}\r\n"
        f"Subject: {subject}\r\n"
        "\r\n"
        "body\r\n"
    ).encode()


class FakeIMAP:
    """Records UID commands; fails loudly on any sequence-number command."""

    def __init__(self, messages, copy_status="OK"):
        # messages: {uid (bytes): raw RFC 822 bytes}
        self.messages = messages
        self.copy_status = copy_status
        self.calls = []

    def __call__(self, host):
        return self

    def login(self, user, password):
        return "OK", [b""]

    def select(self, mailbox, readonly=False):
        return "OK", [b"1"]

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
            return "OK", [b""]
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


ACCOUNT = {"id": "test", "imap_host": "imap.example.com", "username": "u", "password": "p"}
FOLDERS = {"shop": "Shopping", "catch_all": "Catch All"}


def run_account(fake, rules):
    with mock.patch.object(mail_filter.imaplib, "IMAP4_SSL", fake), \
            mock.patch.object(mail_filter, "DRY_RUN", False), \
            redirect_stdout(io.StringIO()):
        mail_filter.process_account(ACCOUNT, FOLDERS, rules)


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
            rule({"to_prefix": ["shop-"]}, {"move": "shop", "mark_read": True}),
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
            rule({"to_prefix": ["shop-"]}, {"move": "shop"}),
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
            rule({"to_prefix": ["shop-"]}, {"move": "shop"}),
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
            "rules": [rule({"to_prefix": ["shop-"]}, {"move": "shop"})],
            "catch_all": {"move": "catch_all"},
        })

        self.assertEqual(fake.message_calls(), [
            ("UID", "COPY", b"300", '"Catch All"'),
            ("UID", "STORE", b"300", "+FLAGS", "(\\Deleted)"),
        ])

    def test_no_rule_and_no_catch_all(self):
        fake = FakeIMAP({b"300": raw_message(to="random@example.com")})
        run_account(fake, {"rules": [rule({"to_prefix": ["shop-"]}, {"move": "shop"})]})

        self.assertEqual(fake.message_calls(), [])

    def test_rule_without_action(self):
        fake = FakeIMAP({b"300": raw_message(to="shop-a@example.com")})
        run_account(fake, {"rules": [rule({"to_prefix": ["shop-"]}, {})]})

        self.assertEqual(fake.message_calls(), [])


class ChooseRuleTests(unittest.TestCase):

    def choose(self, rules_cfg, addresses=(), subject="", from_addr="", from_name=""):
        with redirect_stdout(io.StringIO()):
            return mail_filter.choose_rule(list(addresses), subject, from_addr, from_name, rules_cfg)

    def test_first_match_wins(self):
        cfg = {"rules": [
            rule({"to": ["shop-amazon"]}, {"move": "amazon"}, name="specific"),
            rule({"to_prefix": ["shop-"]}, {"move": "shop"}, name="broad"),
        ]}
        self.assertEqual(self.choose(cfg, ["shop-amazon@example.com"])["name"], "specific")
        self.assertEqual(self.choose(cfg, ["shop-ebay@example.com"])["name"], "broad")

    def test_matchers(self):
        cases = [
            ({"to": ["me"]}, {"addresses": ["Me@Example.com"]}),
            ({"to_prefix": ["dev-"]}, {"addresses": ["dev-github@example.com"]}),
            ({"from_contains": ["github.com"]}, {"from_addr": "noreply@github.com"}),
            ({"from_name_contains": ["github"]}, {"from_name": "github notifications"}),
            ({"subject_contains": ["invoice"]}, {"subject": "Your Invoice #1"}),
            ({"subject_equals": "Hello"}, {"subject": "hello"}),
        ]
        for match, kwargs in cases:
            with self.subTest(match=match):
                cfg = {"rules": [rule(match, {"move": "x"})]}
                self.assertIsNotNone(self.choose(cfg, **kwargs))

    def test_string_value_is_one_token_not_characters(self):
        # Regression: a bare string was iterated character by character,
        # so "rachellehmannhaupt" matched almost any sender.
        cfg = {"rules": [rule({"from_contains": "rachellehmannhaupt"}, {"move": "x"})]}
        self.assertIsNotNone(self.choose(cfg, from_addr="rachellehmannhaupt@example.com"))
        self.assertIsNone(self.choose(cfg, from_addr="verizon-notifications@verizon.com"))

    def test_string_and_list_values_are_equivalent(self):
        cases = [
            ("to", "me", {"addresses": ["me@example.com"]}, {"addresses": ["mae@example.com"]}),
            ("to_prefix", "dev-", {"addresses": ["dev-x@example.com"]}, {"addresses": ["d@example.com"]}),
            ("from_contains", "github.com", {"from_addr": "n@github.com"}, {"from_addr": "hub@tig.com"}),
            ("from_name_contains", "github", {"from_name": "github bot"}, {"from_name": "big hut"}),
            ("subject_contains", "invoice", {"subject": "Invoice #1"}, {"subject": "voice note"}),
            ("subject_equals", "hello", {"subject": "Hello"}, {"subject": "hell"}),
        ]
        for field, value, hit, miss in cases:
            for form in (value, [value]):
                with self.subTest(field=field, form=form):
                    cfg = {"rules": [rule({field: form}, {"move": "x"})]}
                    self.assertIsNotNone(self.choose(cfg, **hit))
                    self.assertIsNone(self.choose(cfg, **miss))

    def test_non_matching(self):
        cfg = {"rules": [rule({"subject_equals": ["Hello"]}, {"move": "x"})]}
        self.assertIsNone(self.choose(cfg, subject="Hello there"))

    def test_move_beats_delete(self):
        cfg = {"rules": [rule({}, {"move": "x", "delete": True, "mark_read": True})]}
        result = self.choose(cfg)
        self.assertEqual(result["move"], "x")
        self.assertFalse(result["delete"])
        self.assertTrue(result["mark_read"])

    def test_catch_all_fallback(self):
        cfg = {"rules": [rule({"to": ["me"]}, {"move": "x"})],
               "catch_all": {"move": "catch_all", "mark_read": False}}
        self.assertEqual(self.choose(cfg, ["other@example.com"]), {
            "name": "<catch_all>", "move": "catch_all", "delete": False, "mark_read": False,
        })


if __name__ == "__main__":
    unittest.main()
