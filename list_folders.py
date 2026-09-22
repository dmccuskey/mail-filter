import sys
import imaplib

try:
    import json5 as json_parser
except ImportError:
    import json as json_parser


CONFIG_FILE = "accounts.local.json5"

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

# Find matching account
account = next((acc for acc in config.get("accounts", []) if acc.get("id") == target_id), None)

if not account:
    print(f"Error: Account ID '{target_id}' not found in {CONFIG_FILE}.")
    sys.exit(1)

HOST = account["imap_host"]
USER = account["username"]
PASSWORD = account["password"]


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

