#!/usr/bin/env python3
import imaplib
import email
import re
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


def get_recipients(msg):
    """Collect all recipient addresses from common headers, lowercased."""
    fields = []
    for header in ["To", "Cc", "Delivered-To", "X-Original-To"]:
        v = msg.get(header)
        if v:
            fields.append(v)
    return [addr.lower() for _, addr in getaddresses(fields)]


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
        if entry is None:  # imaplib's result when there are no mailboxes
            continue
        if isinstance(entry, tuple):
            head, literal_name = entry[0], entry[1]
        else:
            head, literal_name = entry, None
        match = LIST_LINE.match(head)
        if not match:
            continue
        if b"\\trash" not in match.group("attrs").lower().split():
            continue
        name = literal_name if literal_name is not None else match.group("name")
        if literal_name is None and name.startswith(b'"') and name.endswith(b'"'):
            name = name[1:-1]
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


def contains_all(haystack: str, tokens):
    """Return True if all tokens (case-insensitive) are contained in haystack."""
    if not tokens:
        return True
    h = haystack.lower()
    for t in tokens:
        if t.lower() not in h:
            return False
    return True


def contains_any(haystack: str, tokens):
    """Return True if at least one token (case-insensitive) is contained in haystack."""
    if DEV_LOGS: log(f"{tokens}")
    if not tokens:
        return True
    h = haystack.lower()
    for t in tokens:
        if DEV_LOGS: log(f"{h} {t}")
        if t.lower() in h:
            return True
    return False


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


def local_part_matches(addresses, match_cfg):
    """
    Match based on local-part:
      - 'to': exact local names
      - 'to_prefix': list of prefixes for local names
    If neither is present → no address constraint.
    """
    to_list = [x.lower() for x in as_list(match_cfg.get("to")) if x]
    prefixes = [x.lower() for x in as_list(match_cfg.get("to_prefix")) if x]

    # If no local-part constraints, automatically ok
    if not to_list and not prefixes:
        return True

    if DEV_LOGS: log(f"{to_list}")

    for addr in addresses:
        local, _, _ = addr.partition("@")
        local = local.lower()

        if to_list and local in to_list:
            return True

        if prefixes:
            for p in prefixes:
                if local.startswith(p):
                    return True

    return False


def choose_rule(addresses, subject, from_addr, from_name, rules_cfg):
    """
    Unified rule engine with per-account config and match/do structure.

    rules_cfg example:
    {
      "rules": [ { "name": "...", "match": {...}, "do": {...} }, ... ],
      "catch_all": { "move": "catch_all", "mark_read": false }
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
    subject_lower = (subject or "").lower()
    from_lower = (from_addr or "").lower()
    from_name_lower = (from_name or "").lower()

    rules = rules_cfg.get("rules", [])
    catch_all_cfg = rules_cfg.get("catch_all")

    for rule in rules:
        rule_name = rule.get("name", "<unnamed>")
        match_cfg = rule.get("match", {}) or {}
        do_cfg = rule.get("do", {}) or {}

        # 1) Address matching (local-part based)
        if not local_part_matches(addresses, match_cfg):
            continue

        # 2) From address constraints
        from_tokens = as_list(match_cfg.get("from_contains"))
        if from_tokens and not contains_any(from_lower, from_tokens):
            continue

        # 3) From name constraints
        from_name_tokens = as_list(match_cfg.get("from_name_contains"))
        if from_name_tokens and not contains_any(from_name_lower, from_name_tokens):
            continue

        # 4) Subject constraints
        subj_tokens = as_list(match_cfg.get("subject_contains"))
        if subj_tokens and not contains_any(subject_lower, subj_tokens):
            continue

        # 4b) Subject equals constraints
        subj_equals = as_list(match_cfg.get("subject_equals"))
        if subj_equals:
            if not any(subject_lower == target.lower() for target in subj_equals):
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
    # subject_lower = (subject or "").lower()
    # from_lower = (from_addr or "").lower()

    # rules = rules_cfg.get("rules", [])
    # catch_all_cfg = rules_cfg.get("catch_all")

    # for rule in rules:
    #     rule_name = rule.get("name", "<unnamed>")
    #     match_cfg = rule.get("match", {}) or {}
    #     do_cfg = rule.get("do", {}) or {}

    #     if DEV_LOGS: log(f"addresses")
    #     # 1) Address matching (local-part based)
    #     if not local_part_matches(addresses, match_cfg):
    #         continue

    #     if DEV_LOGS: log(f"from constrains")
    #     # 2) From constraints
    #     from_tokens = match_cfg.get("from_contains", [])
    #     if not contains_any(from_lower, from_tokens):
    #         continue

    #     if DEV_LOGS: log(f"subject constrains")
    #     # 3) Subject constraints
    #     subj_tokens = match_cfg.get("subject_contains", [])
    #     if not contains_any(subject_lower, subj_tokens):
    #         continue

    #     # If we reach here, rule matches
    #     move_key = do_cfg.get("move")
    #     delete_flag = bool(do_cfg.get("delete", False))
    #     mark_read = bool(do_cfg.get("mark_read", False))

    #     # If move is present, ignore delete (move wins)
    #     if move_key is not None and delete_flag:
    #         log(
    #             f"[{rule_name}] Warning: rule has both move and delete=true; "
    #             "ignoring delete"
    #         )
    #         delete_flag = False

    #     return {
    #         "name": rule_name,
    #         "move": move_key,
    #         "delete": delete_flag,
    #         "mark_read": mark_read,
    #     }

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
      2. otherwise the mailbox the server advertises with \Trash
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
    password = account_cfg["password"]

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

        # Recipients (To/Cc/etc.)
        addresses = get_recipients(msg)

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
        # rule = choose_rule(addresses, subject, from_addr, rules_cfg)
        rule = choose_rule(addresses, subject, from_addr, from_name, rules_cfg)
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

    accounts_cfg = load_json(base / "accounts.local.json5")
    folders_cfg_all = load_json(base / "folders.local.json5")["folders"]
    rules_all = load_json(base / "rules.local.json5")

    for account in accounts_cfg["accounts"]:
        account_id = account["id"]
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
