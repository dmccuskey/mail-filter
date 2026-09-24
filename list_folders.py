import sys
import imaplib
from pathlib import Path

from mail_filter import account_password, password_problems

try:
    import json5 as json_parser
except ImportError:
    import json as json_parser


CONFIG_FILE = Path(__file__).resolve().parent / "accounts.local.json5"

if len(sys.argv) < 2:
    print("Usage: python list_folders.py <account_id>")
    sys.exit(1)

target_id = sys.argv[1]

# Load and parse the config file
try:
    with open(CONFIG_FILE, "r") as f:
        config = json_parser.load(f)
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
    line = m.decode(errors="ignore")
    print(line)

print("\n--- Extracted mailbox names ---\n")

for m in mailboxes:
    line = m.decode(errors="ignore")

    # Try to extract just the name portion
    if ' "/" ' in line:
        parts = line.split(' "/" ')
        name = parts[-1].strip().strip('"')
    else:
        name = line

    print(name)

imap.logout()

