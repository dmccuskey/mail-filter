# Mail Filter

Filter your mail with simple rules. No programming required.

Mail Filter connects to one or more IMAP accounts, checks each unread message in the inbox against rules you write in a small config file, and moves, trashes, deletes, or marks it read. It runs on a schedule on any machine with Python 3: a Raspberry Pi, a Mac, a Linux server, or a Windows PC.

```json5
{
  "name": "GitHub notifications",
  "match": { "from_email_ends_with": "@github.com" },
  "do": { "move": "github", "mark_read": true }
}
```

## Features

- Rules match the recipient, the sender's address or name, and the subject: exact, contains, starts with, or ends with
- The first matching rule wins; an optional catch-all handles everything else
- Actions: move, trash, delete, mark read
- Multiple accounts, each with its own rules
- Rules name folders by a short key, so a renamed mailbox is fixed in one place
- A dry run shows what each rule would do without changing anything
- Mistakes in the rules are caught before any mail is touched
- One readable log line per message
- Works with Gmail and with standard IMAP servers
- Nothing to install beyond Python 3

## Quick Start

This gets one account filtering with one rule in about 15 minutes, on a Mac or Linux. It takes the shortest path; each step ends with where to go for more. Windows, and more detail for each system, are in [Installation](docs/installation.md).

You need Python 3.8 or later (`python3 --version`), `git`, and the IMAP login for a mail account.

### 1. Get the code

```bash
git clone https://github.com/dmccuskey/mail-filter.git
cd mail-filter
```

There is nothing to install: Mail Filter uses only Python's standard library and the JSON5 parser bundled in `vendor/`.

### 2. Add your account

Create `accounts.local.json5` with your account:

```json5
{
  "my-mail": {
    "imap_host": "imap.gmail.com",
    "username": "you@gmail.com",
    "password": "your-app-password",
    "dry_run": true
  }
}
```

- `my-mail` is an ID you choose; the other config files use it to refer to this account.
- `imap_host` is your provider's IMAP server. Search for "*provider* IMAP settings" if you don't know it.
- `"dry_run": true` means nothing on the server will be changed until you remove it (step 6).

The `.local.json5` files are ignored by Git, so your password stays out of the repository. Keep the file private with `chmod 600 accounts.local.json5`.

**App passwords.** Many providers don't accept your normal password from a program like this; instead you create an *app password* in your account's security settings and use that. Gmail (requires 2-Step Verification), iCloud ("app-specific password"), Yahoo, AOL, and Zoho require one; Fastmail recommends one. Proton Mail works only through Proton Mail Bridge. Outlook.com, Hotmail, and Microsoft 365 no longer accept passwords over IMAP at all (they require OAuth), so Mail Filter can't connect to them.

**Going further:** keep the password out of the file with [`password_env`](docs/configuration.md#password_env), and see [Credentials](docs/operations.md#credentials).

### 3. Test the connection

```bash
python3 list_folders.py my-mail
```

This logs in and lists your mailboxes. If it fails, check the host, the username, and the app password.

### 4. Choose a folder

In your mail client, create a folder (on Gmail, a label) called `My Filtered Folder`. Run `python3 list_folders.py my-mail` again and find it under "Extracted mailbox names". Copy its full name exactly as listed: on Gmail it is `My Filtered Folder`, but many other servers add a prefix, such as `INBOX.My Filtered Folder`.

Create `folders.local.json5`. It maps a short key, which your rules use, to that full mailbox name:

```json5
{
  "my-mail": {
    "filtered_key": "My Filtered Folder"
  }
}
```

Here `filtered_key` is the key (any name you like), and `"My Filtered Folder"` is the mailbox name from `list_folders.py`.

**Going further:** [Folder Mapping](docs/configuration.md#folder-mapping), including where trash goes.

### 5. Write your first rule

Create `rules.local.json5`:

```json5
{
  "my-mail": {
    "rules": [
      {
        "name": "Hello World",
        "match": { "subject_contains": "hello mail-filter" },
        "do": { "move": "filtered_key" }
      }
    ]
  }
}
```

The rule moves matching messages to the folder with the key `filtered_key`. Matching ignores case, so this matches any subject containing "Hello Mail-Filter".

**Going further:** the [Rule Reference](docs/rule-reference.md) lists every match field and action, how rules combine, and the catch-all.

### 6. Try it

Send yourself a message with the subject `Hello mail-filter`, and leave it unread: Mail Filter only looks at unread messages in the inbox. Then run:

```bash
python3 mail_filter.py
```

```text
[2026-09-25 10:12:03] [my-mail] Connecting to imap.gmail.com as you@gmail.com
[2026-09-25 10:12:03] [my-mail] dry_run is true; no changes will be made on the server
[2026-09-25 10:12:04] [my-mail] Gmail IMAP extensions detected (X-GM-EXT-1)
[2026-09-25 10:12:04] [my-mail] Found 3 unseen messages
[2026-09-25 10:12:04] [my-mail] MATCHED #4821 RULE='Hello World' SUBJECT='Hello mail-filter' → My Filtered Folder [DRY_RUN]
[2026-09-25 10:12:04] [my-mail] NO_RULE #4822 SUBJECT='Your order has shipped' [DRY_RUN]
[2026-09-25 10:12:04] [my-mail] NO_RULE #4823 SUBJECT='Lunch on Friday?' [DRY_RUN]
[2026-09-25 10:12:05] [my-mail] Done
```

Each unread message gets one line: which rule matched and what would happen to it. `NO_RULE` messages are left alone.

If a line near the top says `Warning: folder 'My Filtered Folder' (key 'filtered_key') not found`, the mailbox name in `folders.local.json5` doesn't exactly match the one from `list_folders.py`. A dry run still shows `→ My Filtered Folder`, but for real the move would fail and the message would stay in the inbox.

When the lines look right, remove `"dry_run": true` from `accounts.local.json5` and run it again. The message moves to `My Filtered Folder`, and its line no longer ends in `[DRY_RUN]`.

**Going further:** [Log Format](docs/operations.md#log-format) explains every outcome; set `dry_run` again whenever you change your rules.

### 7. Run it on a schedule

Find the full paths to use:

```bash
pwd              # e.g. /home/you/mail-filter
which python3    # e.g. /usr/bin/python3
```

Run `crontab -e` and add a line that runs the filter every 5 minutes, using those paths:

```cron
*/5 * * * * /usr/bin/python3 /home/you/mail-filter/mail_filter.py >> /home/you/mail-filter/mail_filter.log 2>&1
```

The filter's output is appended to `mail_filter.log`; check it with `tail mail_filter.log`.

**Going further:** on a Mac, cron can't read folders such as `Documents` without Full Disk Access, and launchd is the native scheduler; on Windows, use Task Scheduler. Both are in [Installation](docs/installation.md). For a long-running setup, see [Log Rotation](docs/operations.md#log-rotation).

To update later, run `git pull` in the `mail-filter` directory. Your `.local.json5` files are not touched.

## Documentation

- [Installation](docs/installation.md): macOS, Linux and Raspberry Pi, and Windows, including scheduling
- [Configuration](docs/configuration.md): accounts, folders, and rules files
- [Rule Reference](docs/rule-reference.md): match fields, actions, rule order, and the catch-all
- [Operations](docs/operations.md): running, dry runs, logs, cron, credentials, and recovery

Everything else, including how Mail Filter works inside and how it is developed and tested, is listed on the [documentation home](docs/README.md).

## How It Compares

- **[imapfilter](https://github.com/lefcha/imapfilter)** is configured with Lua scripts: very flexible, but you are writing a program. Mail Filter's rules are a list of conditions and actions, checked for mistakes before any mail is touched.
- **Server-side filters** (Sieve, Gmail filters) run on the mail server, but only where the provider supports them, and each provider has its own rule format. Mail Filter works the same way on any IMAP account, and one config covers all your accounts.

## Safety

This software operates on real email. Set `"dry_run": true` on an account whenever you add it or change its rules, and check the log before removing it.

## License

Mail Filter is released under the [MIT License](LICENSE).

It includes [json5](https://github.com/dpranke/pyjson5) (Apache-2.0) in `vendor/json5/`; see [vendor/README.md](vendor/README.md).
