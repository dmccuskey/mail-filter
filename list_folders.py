import sys
import imaplib
from pathlib import Path

from mail_filter import account_password, load_json, parse_list_entry, password_problems


CONFIG_FILE = Path(__file__).resolve().parent / "accounts.local.json5"

if len(sys.argv) < 2:
    print("Usage: python list_folders.py <account_id>")
    sys.exit(1)

target_id = sys.argv[1]

# Load and parse the config file
try:
    config = load_json(CONFIG_FILE)
except FileNotFoundError:
    print(f"Error: Configuration file '{CONFIG_FILE}' not found.")
    sys.exit(1)
except Exception as e:
    print(f"Error parsing '{CONFIG_FILE}': {e}")
    sys.exit(1)

if isinstance(config.get("accounts"), list):
    print(f'Error: {CONFIG_FILE} uses the old format (top-level "accounts" list); '
          "key each account by its ID instead (see docs/configuration.md).")
    sys.exit(1)

# Accounts are keyed by ID
account = config.get(target_id)

if not isinstance(account, dict):
    print(f"Error: Account ID '{target_id}' not found in {CONFIG_FILE}.")
    sys.exit(1)

problems = password_problems(target_id, account)
if problems:
    for problem in problems:
        print(f"Error: {problem}.")
    sys.exit(1)

HOST = account["imap_host"]
USER = account["username"]
PASSWORD = account_password(account)


# HOST = "HOSTNAME"
# USER = "USERNAME"
# PASSWORD = "PASSWORD"

imap = imaplib.IMAP4_SSL(HOST)
imap.login(USER, PASSWORD)

status, mailboxes = imap.list()

print("\n--- Raw IMAP folder list ---\n")

for m in mailboxes:
    if isinstance(m, tuple):  # mailbox name sent as a literal
        m = b" ".join(m)
    if m is not None:
        print(m.decode(errors="ignore"))

print("\n--- Extracted mailbox names ---\n")

for m in mailboxes:
    parsed = parse_list_entry(m)
    if parsed is None:
        continue
    _, name = parsed
    print(name.decode(errors="replace"))

imap.logout()

