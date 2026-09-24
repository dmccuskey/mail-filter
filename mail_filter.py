#!/usr/bin/env python3
import imaplib
import email
import os
import re
import sys
# import json
from email.utils import getaddresses
from email.header import decode_header
from datetime import datetime
from pathlib import Path

try:
    import json5 as json_parser
except ImportError:
    import json as json_parser

DRY_RUN = False  # set to False after you're happy with behavior
DEV_LOGS = False


# ---------- Helpers ----------

def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")


def load_json(path):
    with open(path, "r") as f:
        return json_parser.load(f)


def get_to_addresses(msg):
    """Collect the addresses in the To header only, lowercased."""
    return [addr.lower() for _, addr in getaddresses(msg.get_all("To", [])) if addr]


def ensure_folders_exist(imap, account_id, folder_map):
    """
    For each configured folder, try selecting it.
    If the server says NO, we log a warning.
    """
    for key, folder in folder_map.items():
        # Use quoted name so spaces are safe
        status, _ = imap.select(f'"{folder}"', readonly=True)
        if status != "OK":
            log(
                f"[{account_id}] Warning: folder '{folder}' "
                f"(key '{key}') not found or not selectable (status={status})"
            )

    # Re-select INBOX for normal processing
    imap.select("INBOX")


def detect_gmail(imap):
    """
    Return True if the server advertises Gmail's X-GM-EXT-1 capability,
    False if it does not, or None if the capability query failed.
    None means the provider is unknown; callers must not assume generic IMAP.
    """
    try:
        status, data = imap.capability()
    except (imaplib.IMAP4.error, OSError):
        return None
    if status != "OK" or not data or not data[0]:
        return None
    line = data[0]
    if isinstance(line, str):
        line = line.encode("ascii", errors="ignore")
    # imaplib returns the capabilities as one space-separated line
    return b"X-GM-EXT-1" in line.upper().split()


# A LIST response line as imaplib returns it (no "* LIST" prefix):
#   (\HasNoChildren \Trash) "/" "[Gmail]/Trash"
#   (\HasNoChildren \UnMarked \Trash) "." INBOX.Trash
# A mailbox name sent as a literal arrives as a tuple:
#   (b'(\HasNoChildren \Trash) "/" {9}', b'Corbeille')
LIST_LINE = re.compile(rb'^\((?P<attrs>[^)]*)\) (?:"(?:[^"\\]|\\.)*"|NIL) (?P<name>.+)$')


def parse_list_entry(entry):
    """
    Parse one entry of imaplib's LIST result into (attrs, name) as bytes, or
    return None if it is not a LIST line. The name is exactly the server's
    mailbox name, with quoting removed, whatever the hierarchy delimiter.
    """
    if entry is None:  # imaplib's result when there are no mailboxes
        return None
    if isinstance(entry, tuple):
        head, literal_name = entry[0], entry[1]
    else:
        head, literal_name = entry, None
    match = LIST_LINE.match(head)
    if not match:
        return None
    if literal_name is not None:
        return match.group("attrs"), literal_name
    name = match.group("name")
    if len(name) >= 2 and name.startswith(b'"') and name.endswith(b'"'):
        name = re.sub(rb"\\(.)", rb"\1", name[1:-1])
    return match.group("attrs"), name


def find_advertised_trash(imap):
    """
    Return (mailbox, None) for the one mailbox the server advertises with the
    RFC 6154 \\Trash attribute, or (None, reason) if it cannot be determined.
    The name is returned exactly as the server sent it; nothing is guessed.
    """
    try:
        status, entries = imap.list()
    except (imaplib.IMAP4.error, OSError) as e:
        return None, f"LIST failed ({e})"
    if status != "OK":
        return None, f"LIST failed (status={status})"

    found = []
    for entry in entries or []:
        parsed = parse_list_entry(entry)
        if parsed is None:
            continue
        attrs, name = parsed
        if b"\\trash" not in attrs.lower().split():
            continue
        found.append(name)

    if not found:
        return None, "server advertises no \\Trash mailbox"
    if len(found) > 1:
        return None, f"server advertises {len(found)} \\Trash mailboxes"
    name = found[0]
    # Mailboxes are quoted as "<name>" in COPY; refuse names that cannot be
    # quoted that way rather than risk a malformed command.
    if b'"' in name or b"\\" in name:
        return None, f"advertised \\Trash name {name!r} cannot be quoted safely"
    try:
        return name.decode("ascii"), None
    except UnicodeDecodeError:
        return None, f"advertised \\Trash name {name!r} is not ASCII"


def as_list(value):
    """
    Normalize a match value to a list.
    A single string means one value, not a sequence of characters.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)


def decode_mime_header(value: str) -> str:
    """
    Decode MIME-encoded headers like '=?utf-8?b?...?=' into a readable string.
    Returns a plain Unicode string.
    """
    if not value:
        return ""
    parts = []
    for text, charset in decode_header(value):
        if isinstance(text, bytes):
            # charset may be None; default to utf-8
            try:
                parts.append(text.decode(charset or "utf-8", errors="replace"))
            except LookupError:
                # weird/unknown charset -> best effort
                parts.append(text.decode("utf-8", errors="replace"))
        else:
            parts.append(text)
    return "".join(parts)


class RuleConfigError(ValueError):
    """A rule in the rules config is invalid, e.g. an unknown match field or an empty match."""


# Match fields: each returns the candidate strings for one message.
# to/to_local use the To header only (see docs/rule-reference.md).
MATCH_FIELDS = {
    "to": lambda m: m["to_addresses"],
    "to_local": lambda m: [addr.partition("@")[0] for addr in m["to_addresses"]],
    "from_email": lambda m: [m["from_addr"]],
    "from_name": lambda m: [m["from_name"]],
    "subject": lambda m: [m["subject"]],
}

MATCH_OPS = {
    "is": lambda text, value: text == value,
    "contains": lambda text, value: value in text,
    "starts_with": lambda text, value: text.startswith(value),
    "ends_with": lambda text, value: text.endswith(value),
}

# The supported match field names, e.g. "to_local_starts_with"
MATCHERS = {
    f"{field}_{op}": (field, op) for field in MATCH_FIELDS for op in MATCH_OPS
}


def unknown_match_field_message(index, rule_name, field):
    return f"rule #{index} '{rule_name}' uses unknown match field '{field}'"


def rule_problems(index, rule):
    """
    Return a message for every problem in one rule (empty if none).

    Never ignore a bad field: that would drop a condition. An empty match
    would match every message; catch_all is the way to do that.
    """
    rule_name = rule.get("name", "<unnamed>")
    match_cfg = rule.get("match", {}) or {}
    if not match_cfg:
        return [
            f"rule #{index} '{rule_name}' has an empty match; "
            "use catch_all to act on every unmatched message"
        ]
    problems = []
    for field, values in match_cfg.items():
        if field not in MATCHERS:
            problems.append(unknown_match_field_message(index, rule_name, field))
        elif not any(as_list(values)):
            problems.append(
                f"rule #{index} '{rule_name}' has no values for match field '{field}'"
            )
    return problems


def validate_rules(rules_cfg):
    """Return a message for every problem in every rule (empty if none)."""
    problems = []
    for index, rule in enumerate(rules_cfg.get("rules", []), start=1):
        problems.extend(rule_problems(index, rule))
    return problems


def validate_config_format(accounts_all, folders_all):
    """
    Return a message for every config file problem (empty if none).

    Every config file is an object keyed by account ID; the old
    "accounts" list and "folders" wrapper are rejected, not guessed at.
    """
    problems = []
    if isinstance(accounts_all.get("accounts"), list):
        problems.append(
            'accounts.local.json5 uses the old format (top-level "accounts" list); '
            "key each account by its ID instead (see docs/configuration.md)"
        )
    else:
        for account_id, cfg in accounts_all.items():
            if not isinstance(cfg, dict):
                problems.append(
                    f"account '{account_id}' in accounts.local.json5 must be an object"
                )
            elif not isinstance(cfg.get("mail_enabled", True), bool):
                problems.append(
                    f"account '{account_id}' has an invalid \"mail_enabled\"; "
                    "it must be true or false"
                )

    if "folders" in folders_all and "folders" not in accounts_all:
        problems.append(
            'folders.local.json5 uses the old format (top-level "folders" wrapper); '
            "remove the wrapper so accounts are top-level keys (see docs/configuration.md)"
        )
    return problems


def mail_enabled(cfg):
    """True unless the account sets "mail_enabled": false (the default is true)."""
    return cfg.get("mail_enabled", True)


def password_problems(account_id, cfg, environ=None):
    """
    Return a message for every password problem in one account (empty if none).

    Each account sets exactly one of "password" (the literal password) or
    "password_env" (the name of an environment variable holding it).
    """
    environ = os.environ if environ is None else environ
    has_password = "password" in cfg
    has_env = "password_env" in cfg
    if has_password and has_env:
        return [
            f"account '{account_id}' sets both \"password\" and \"password_env\"; "
            "use only one"
        ]
    if not has_password and not has_env:
        return [f"account '{account_id}' sets neither \"password\" nor \"password_env\""]
    if has_env:
        var = cfg["password_env"]
        if not isinstance(var, str) or not var:
            return [
                f"account '{account_id}' has an invalid \"password_env\"; "
                "it must be an environment variable name"
            ]
        if not environ.get(var):
            return [
                f"account '{account_id}' reads its password from environment variable "
                f"'{var}', which is not set or is empty"
            ]
    return []


def validate_passwords(accounts, environ=None):
    """Return a message for every password problem in every account (empty if none)."""
    problems = []
    for account_id, cfg in accounts.items():
        problems.extend(password_problems(account_id, cfg, environ))
    return problems


def account_password(cfg, environ=None):
    """Return the account's password, from "password" or the "password_env" variable."""
    environ = os.environ if environ is None else environ
    if "password_env" in cfg:
        return environ[cfg["password_env"]]
    return cfg["password"]


def match_field_matches(field, values, message):
    """
    True if any candidate string for the field matches any value
    (case-insensitive). Empty values are ignored; rule_problems rejects
    a field with no non-empty values.
    """
    values = [v.lower() for v in as_list(values) if v]
    if not values:
        return True
    field_name, op_name = MATCHERS[field]
    op = MATCH_OPS[op_name]
    for text in MATCH_FIELDS[field_name](message):
        text = (text or "").lower()
        if any(op(text, value) for value in values):
            return True
    return False


def choose_rule(to_addresses, subject, from_addr, from_name, rules_cfg):
    """
    Unified rule engine with per-account config and match/do structure.

    rules_cfg example:
    {
      "rules": [ { "name": "...", "match": {...}, "do": {...} }, ... ],
      "catch_all": { "move": "archive", "mark_read": false }
    }

    Returns dict:
      {
        "name": rule_name,
        "move": <folder_key or None>,
        "trash": bool,
        "delete": bool,
        "mark_read": bool
      }
    or None if nothing matched and no catch_all.
    """
    message = {
        "to_addresses": list(to_addresses or []),
        "from_addr": from_addr or "",
        "from_name": from_name or "",
        "subject": subject or "",
    }

    rules = rules_cfg.get("rules", [])
    catch_all_cfg = rules_cfg.get("catch_all")

    for index, rule in enumerate(rules, start=1):
        rule_name = rule.get("name", "<unnamed>")
        match_cfg = rule.get("match", {}) or {}
        do_cfg = rule.get("do", {}) or {}

        problems = rule_problems(index, rule)
        if problems:
            raise RuleConfigError(problems[0])

        # Different fields are ANDed; values within a field are ORed.
        if not all(
            match_field_matches(field, values, message)
            for field, values in match_cfg.items()
        ):
            continue

        # If we reach here, rule matches
        move_key = do_cfg.get("move")
        trash_flag = bool(do_cfg.get("trash", False))
        delete_flag = bool(do_cfg.get("delete", False))
        mark_read = bool(do_cfg.get("mark_read", False))

        if move_key is not None and (trash_flag or delete_flag):
            log(
                f"Warning: RULE='{rule_name}' has move with trash/delete; using move"
            )
            trash_flag = False
            delete_flag = False
        elif trash_flag and delete_flag:
            log(f"Warning: RULE='{rule_name}' has trash with delete; using trash")
            delete_flag = False

        return {
            "name": rule_name,
            "move": move_key,
            "trash": trash_flag,
            "delete": delete_flag,
            "mark_read": mark_read,
        }

    # No rule matched → catch-all?
    if catch_all_cfg:
        move_key = catch_all_cfg.get("move")
        trash_flag = bool(catch_all_cfg.get("trash", False))
        delete_flag = bool(catch_all_cfg.get("delete", False))
        mark_read = bool(catch_all_cfg.get("mark_read", False))

        if move_key is not None and (trash_flag or delete_flag):
            log(
                "Warning: RULE='<catch_all>' has move with trash/delete; using move"
            )
            trash_flag = False
            delete_flag = False
        elif trash_flag and delete_flag:
            log("Warning: RULE='<catch_all>' has trash with delete; using trash")
            delete_flag = False

        return {
            "name": "<catch_all>",
            "move": move_key,
            "trash": trash_flag,
            "delete": delete_flag,
            "mark_read": mark_read,
        }

    return None


def resolve_trash_mailbox(imap, account_id, folder_map):
    """
    Resolve the reserved Trash role for an account (ADR 006):
      1. the account's explicit "trash" folder mapping, if present
      2. otherwise the mailbox the server advertises with \\Trash
    Returns (mailbox, None), or (None, reason) if neither is available.
    """
    configured = folder_map.get("trash")
    if configured:
        log(f"[{account_id}] Trash mailbox: '{configured}' (folders config)")
        return configured, None
    mailbox, problem = find_advertised_trash(imap)
    if mailbox:
        log(f"[{account_id}] Trash mailbox: '{mailbox}' (server \\Trash)")
    else:
        log(f"[{account_id}] ERROR: cannot resolve Trash mailbox: {problem}")
    return mailbox, problem


def log_message(account_id, result, msg_ref, subject, rule_name=None, outcome=None):
    """
    Write the single per-message line (format: docs/operations.md):
      [account] MATCHED #uid RULE='name' SUBJECT='subject' <outcome> [DRY_RUN]
      [account] NO_RULE #uid SUBJECT='subject' [DRY_RUN]
    """
    line = f"[{account_id}] {result} {msg_ref}"
    if rule_name is not None:
        line += f" RULE='{rule_name}'"
    line += f" SUBJECT='{subject}'"
    if outcome:
        line += f" {outcome}"
    if DRY_RUN:
        line += " [DRY_RUN]"
    log(line)


def apply_mark_read(imap, uid):
    """
    Set \\Seen before a copy/delete, so a generic copy carries it.
    Returns (extras, read_note): the suffix for a successful outcome, and the
    note for a FAILED outcome saying the message was left marked read.
    """
    status, _ = imap.uid("STORE", uid, "+FLAGS", "(\\Seen)")
    if status != "OK":
        return f" (mark_read FAILED: status={status})", ""
    return " (mark_read)", " (marked read)"


def move_message(imap, uid, mailbox, destination, is_gmail, mark_read):
    """
    Move (or trash) one message out of INBOX, identified by UID.
    Returns (outcome, expunge_needed).
    """
    if DRY_RUN:
        return f"→ {destination}" + (" (mark_read)" if mark_read else ""), False

    extras, read_note = apply_mark_read(imap, uid) if mark_read else ("", "")

    status, _ = imap.uid("COPY", uid, f'"{mailbox}"')
    if status != "OK":
        return (
            f"FAILED: copy to '{mailbox}' refused (status={status}); "
            f"left in INBOX{read_note}"
        ), False

    if is_gmail:
        # Gmail folders are labels: COPY added the destination label;
        # removing \\Inbox takes the message out of INBOX while keeping
        # its other labels. No \\Deleted, no EXPUNGE. On failure the
        # message stays in INBOX; never fall back to \\Deleted.
        status, _ = imap.uid("STORE", uid, "-X-GM-LABELS", r"(\Inbox)")
        if status != "OK":
            return (
                f"FAILED: \\Inbox label not removed (status={status}); "
                f"copied to '{mailbox}', still in INBOX{read_note}"
            ), False
        return f"→ {destination}{extras}", False

    # Generic IMAP move: mark source for removal and expunge once
    # after processing all messages.
    status, _ = imap.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
    if status != "OK":
        return (
            f"FAILED: \\Deleted not set (status={status}); "
            f"copied to '{mailbox}', still in INBOX{read_note}"
        ), False
    return f"→ {destination}{extras}", True


def delete_message(imap, uid, mark_read):
    """Mark one message \\Deleted for the end-of-run EXPUNGE. Returns (outcome, expunge_needed)."""
    if DRY_RUN:
        return "DELETED" + (" (mark_read)" if mark_read else ""), False

    extras, read_note = apply_mark_read(imap, uid) if mark_read else ("", "")

    status, _ = imap.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
    if status != "OK":
        return f"FAILED: \\Deleted not set (status={status}); left in INBOX{read_note}", False
    return f"DELETED{extras}", True


def mark_message_read(imap, uid):
    """mark_read on its own: set \\Seen and leave the message in INBOX. Returns the outcome."""
    if DRY_RUN:
        return "MARKED_READ"
    status, _ = imap.uid("STORE", uid, "+FLAGS", "(\\Seen)")
    if status != "OK":
        return f"FAILED: \\Seen not set (status={status}); left in INBOX"
    return "MARKED_READ"


# ---------- Core processing ----------

def process_account(account_cfg, folders_for_account, rules_cfg):
    account_id = account_cfg["id"]
    host = account_cfg["imap_host"]
    user = account_cfg["username"]
    password = account_password(account_cfg)

    log(f"[{account_id}] Connecting to {host} as {user}")
    imap = imaplib.IMAP4_SSL(host)
    imap.login(user, password)

    # Gmail moves messages by label, so move/trash need to know the provider.
    # None (query failed) blocks move/trash rather than risking generic
    # \Deleted + EXPUNGE semantics on Gmail.
    is_gmail = detect_gmail(imap)
    if is_gmail is None:
        log(
            f"[{account_id}] ERROR: capability query failed; "
            "move and trash actions will be skipped"
        )
    elif is_gmail:
        log(f"[{account_id}] Gmail IMAP extensions detected (X-GM-EXT-1)")

    # Check folder existence
    ensure_folders_exist(imap, account_id, folders_for_account)
    expunge_needed = False

    # Trash is a reserved role, resolved once, only when a trash rule fires:
    # the account's "trash" mapping if present, else the server's \Trash.
    trash_mailbox = None
    trash_problem = None
    trash_resolved = False

    status, _ = imap.select("INBOX")
    if status != "OK":
        log(f"[{account_id}] ERROR: cannot select INBOX (status={status})")
        imap.logout()
        return

    # Only act on unseen messages so we don't re-process old mail
    # UIDs (not sequence numbers) so message identity survives EXPUNGE
    status, msgs = imap.uid("SEARCH", "UNSEEN")
    if status != "OK":
        log(f"[{account_id}] ERROR: search UNSEEN failed (status={status})")
        imap.logout()
        return

    ids = msgs[0].split()
    log(f"[{account_id}] Found {len(ids)} unseen messages")

    for uid in ids:
        # Log UIDs as #42 (uid itself stays bytes for IMAP commands)
        msg_ref = "#" + uid.decode("ascii", errors="replace")

        # Use PEEK so we don't mark as Seen just by fetching
        status, data = imap.uid("FETCH", uid, "(BODY.PEEK[])")
        if status != "OK":
            log(f"[{account_id}] ERROR: fetch {msg_ref} failed (status={status})")
            continue

        msg = email.message_from_bytes(data[0][1])

        # Recipients (To header only)
        to_addresses = get_to_addresses(msg)

        # Subject
        # subject = msg.get("Subject", "") or ""
        # Subject (decoded from MIME form)
        subject_raw = msg.get("Subject", "") or ""
        subject = decode_mime_header(subject_raw)
        if DEV_LOGS: log(f"[{account_id}] Decoded subject: {subject}")

        # From (single address)
        # from_raw = msg.get("From", "") or ""
        # from_addrs = [a.lower() for _, a in getaddresses([from_raw])]
        # from_addr = from_addrs[0] if from_addrs else ""

        # From (parsed into display name and email address)
        from_raw = msg.get("From", "") or ""
        parsed_from = getaddresses([from_raw])
        if parsed_from:
            from_name_raw, from_addr = parsed_from[0]
            from_name = decode_mime_header(from_name_raw).lower()
            from_addr = from_addr.lower()
        else:
            from_name = ""
            from_addr = ""

        # Decide rule
        rule = choose_rule(to_addresses, subject, from_addr, from_name, rules_cfg)
        if not rule:
            log_message(account_id, "NO_RULE", msg_ref, subject)
            continue

        rule_name = rule.get("name", "<unnamed>")
        move_key = rule.get("move")
        trash_flag = rule.get("trash", False)
        delete_flag = rule.get("delete", False)
        mark_read = rule.get("mark_read", False)

        if move_key is not None or trash_flag:
            # --- MOVE or TRASH action ---
            if move_key is not None:
                target_mailbox = folders_for_account.get(move_key)
                problem = f"no folder mapping for key '{move_key}'"
            else:
                if not trash_resolved:
                    trash_mailbox, trash_problem = resolve_trash_mailbox(
                        imap, account_id, folders_for_account
                    )
                    trash_resolved = True
                target_mailbox = trash_mailbox
                problem = f"no Trash mailbox: {trash_problem}"

            if not target_mailbox:
                outcome = f"SKIPPED: {problem}"
            elif is_gmail is None:
                outcome = "SKIPPED: provider unknown (capability query failed)"
            else:
                destination = f"Trash ({target_mailbox})" if trash_flag else target_mailbox
                outcome, expunge = move_message(
                    imap, uid, target_mailbox, destination, is_gmail, mark_read
                )
                expunge_needed = expunge_needed or expunge
        elif delete_flag:
            # --- DELETE action ---
            outcome, expunge = delete_message(imap, uid, mark_read)
            expunge_needed = expunge_needed or expunge
        elif mark_read:
            # --- MARK_READ on its own (message stays in INBOX) ---
            outcome = mark_message_read(imap, uid)
        else:
            outcome = "NO_ACTION"

        log_message(account_id, "MATCHED", msg_ref, subject, rule_name, outcome)

    if not DRY_RUN and expunge_needed:
        imap.expunge()

    imap.logout()
    log(f"[{account_id}] Done")


def main():
    base = Path(__file__).resolve().parent  # directory containing this script

    accounts_all = load_json(base / "accounts.local.json5")
    folders_cfg_all = load_json(base / "folders.local.json5")
    rules_all = load_json(base / "rules.local.json5")

    # Reject a malformed or old-format config before connecting to any account
    format_problems = validate_config_format(accounts_all, folders_cfg_all)
    for problem in format_problems:
        log(f"ERROR: {problem}")
    if format_problems:
        log("ERROR: configuration format problem(s); no mail processed")
        sys.exit(1)

    # Disabled accounts are skipped entirely, including the startup checks,
    # so a broken or half-configured account can be disabled
    enabled_accounts = {
        account_id: cfg for account_id, cfg in accounts_all.items() if mail_enabled(cfg)
    }

    # Reject a missing or ambiguous password before connecting to any account
    password_problem_list = validate_passwords(enabled_accounts)
    for problem in password_problem_list:
        log(f"ERROR: {problem}")
    if password_problem_list:
        log("ERROR: account password problem(s) in accounts.local.json5; no mail processed")
        sys.exit(1)

    accounts = [{**cfg, "id": account_id} for account_id, cfg in accounts_all.items()]

    # Reject invalid rules before connecting to any account
    problem_count = 0
    for account in accounts:
        if not mail_enabled(account):
            continue
        account_rules = rules_all.get(account["id"])
        for problem in validate_rules(account_rules or {}):
            log(f"[{account['id']}] ERROR: {problem}")
            problem_count += 1
    if problem_count:
        log(
            f"ERROR: {problem_count} rule problem(s) in rules.local.json5; "
            "no mail processed (see docs/rule-reference.md)"
        )
        sys.exit(1)

    for account in accounts:
        account_id = account["id"]
        if not mail_enabled(account):
            log(f"[{account_id}] mail_enabled is false; skipping account")
            continue

        account_folders = folders_cfg_all.get(account_id)
        account_rules = rules_all.get(account_id)

        if not account_folders:
            log(f"[{account_id}] Warning: no folder config found; skipping account")
            continue

        if not account_rules:
            log(f"[{account_id}] Warning: no rules config found; skipping account")
            continue

        process_account(account, account_folders, account_rules)


if __name__ == "__main__":
    main()
