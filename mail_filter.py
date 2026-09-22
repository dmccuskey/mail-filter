#!/usr/bin/env python3
import imaplib
import email
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
        delete_flag = bool(do_cfg.get("delete", False))
        mark_read = bool(do_cfg.get("mark_read", False))

        if move_key is not None and delete_flag:
            log(
                f"[{rule_name}] Warning: rule has both move and delete=true; "
                "ignoring delete"
            )
            delete_flag = False

        return {
            "name": rule_name,
            "move": move_key,
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
        delete_flag = bool(catch_all_cfg.get("delete", False))
        mark_read = bool(catch_all_cfg.get("mark_read", False))

        if move_key is not None and delete_flag:
            log(
                "[catch_all] Warning: catch_all has both move and delete=true; "
                "ignoring delete"
            )
            delete_flag = False

        return {
            "name": "<catch_all>",
            "move": move_key,
            "delete": delete_flag,
            "mark_read": mark_read,
        }

    return None


# ---------- Core processing ----------

def process_account(account_cfg, folders_for_account, rules_cfg):
    account_id = account_cfg["id"]
    host = account_cfg["imap_host"]
    user = account_cfg["username"]
    password = account_cfg["password"]

    log(f"[{account_id}] Connecting to {host} as {user}")
    imap = imaplib.IMAP4_SSL(host)
    imap.login(user, password)

    # Check folder existence
    ensure_folders_exist(imap, account_id, folders_for_account)

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
        # Use PEEK so we don't mark as Seen just by fetching
        status, data = imap.uid("FETCH", uid, "(BODY.PEEK[])")
        if status != "OK":
            log(f"[{account_id}] ERROR: fetch {uid} failed (status={status})")
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
            log(f"[{account_id}] No rule for message {uid} (subject='{subject}')")
            continue

        rule_name = rule.get("name", "<unnamed>")
        move_key = rule.get("move")
        delete_flag = rule.get("delete", False)
        mark_read = rule.get("mark_read", False)

        # --- MOVE action (if present) ---
        if move_key is not None:
            target_mailbox = folders_for_account.get(move_key)
            if not target_mailbox:
                log(
                    f"[{account_id}] [{rule_name}] No folder mapping for key "
                    f"'{move_key}'"
                )
                continue

            log(
                f"[{account_id}] [{rule_name}] Message {uid}: '{subject}' "
                f"→ {target_mailbox} (mark_read={mark_read})"
            )

            if DRY_RUN:
                continue

            if mark_read:
                imap.uid("STORE", uid, "+FLAGS", "(\\Seen)")

            status, _ = imap.uid("COPY", uid, f'"{target_mailbox}"')
            if status != "OK":
                log(
                    f"[{account_id}] [{rule_name}] ERROR: copy to "
                    f"'{target_mailbox}' failed (status={status})"
                )
                continue

            # Mark original as deleted; expunge at end
            imap.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
            continue

        # --- DELETE action (only if no move) ---
        if delete_flag and move_key is None:
            log(
                f"[{account_id}] [{rule_name}] Message {uid}: '{subject}' "
                f"→ DELETE (mark_read={mark_read})"
            )

            if DRY_RUN:
                continue

            if mark_read:
                imap.uid("STORE", uid, "+FLAGS", "(\\Seen)")

            imap.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
            continue

        # No move, no delete -> nothing to do
        log(
            f"[{account_id}] [{rule_name}] Message {uid}: '{subject}' "
            f"→ no action (rule has neither move nor delete)"
        )

    if not DRY_RUN:
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