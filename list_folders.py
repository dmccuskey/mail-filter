import imaplib

HOST = "HOSTNAME"
USER = "USERNAME"
PASSWORD = "PASSWORD"

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

