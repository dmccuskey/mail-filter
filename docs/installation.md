# Mail Filter Installation

The [Quick Start](../README.md#quick-start) is the short path on a Mac or Linux. This page has the details for each system: getting Python and Git, running the commands, scheduling the filter, and updating it.

Mail Filter needs only Python 3.8 or later and Git. It uses Python's standard library plus a JSON5 parser bundled in `vendor/`, so there are no packages to install and no virtual environment is needed.

## Where to Run It

The filter checks for mail each time it runs, so run it on a machine that is on whenever you want mail filtered. A Raspberry Pi or a home server is ideal. A laptop works, but filtering pauses while it sleeps and catches up on the next run.

## macOS

### Python and Git

macOS includes `python3` and `git` with the Command Line Tools. The first time you run either, macOS offers to install them, or you can install them directly:

```bash
xcode-select --install
python3 --version
```

Python from [Homebrew](https://brew.sh) (`brew install python`) or [python.org](https://www.python.org/downloads/) also works.

### Where to Put It

Clone Mail Filter into a folder outside `Documents`, `Desktop`, `Downloads`, and iCloud Drive, for example `~/mail-filter`. macOS protects those folders, and a scheduled job cannot read them unless it is given Full Disk Access.

```bash
cd ~
git clone https://github.com/dmccuskey/mail-filter.git
cd mail-filter
```

Then continue with [step 2 of the Quick Start](../README.md#2-add-your-account).

### Scheduling with cron

cron works as in the Quick Start. `which python3` shows the path to use: `/usr/bin/python3` for the Command Line Tools, `/opt/homebrew/bin/python3` for Homebrew on Apple silicon.

```cron
*/5 * * * * /usr/bin/python3 /Users/you/mail-filter/mail_filter.py >> /Users/you/mail-filter/mail_filter.log 2>&1
```

cron skips runs while the Mac is asleep. If Mail Filter must live in a protected folder, give `/usr/sbin/cron` Full Disk Access in System Settings → Privacy & Security.

### Scheduling with launchd

launchd is the macOS scheduler. After the Mac wakes, it makes one run to cover the runs missed during sleep.

Create `~/Library/LaunchAgents/local.mail-filter.plist`, replacing `/Users/you` with your home folder:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>local.mail-filter</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/Users/you/mail-filter/mail_filter.py</string>
  </array>
  <key>StartInterval</key>
  <integer>300</integer>
  <key>StandardOutPath</key>
  <string>/Users/you/mail-filter/mail_filter.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/you/mail-filter/mail_filter.log</string>
</dict>
</plist>
```

`StartInterval` is in seconds, so `300` runs the filter every 5 minutes. Load it:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.mail-filter.plist
```

To stop it, or before editing the file:

```bash
launchctl bootout gui/$(id -u)/local.mail-filter
```

## Linux and Raspberry Pi

### Python and Git

Most distributions, including Raspberry Pi OS, include Python 3. Install Git if it is missing:

```bash
sudo apt install git python3      # Debian, Ubuntu, Raspberry Pi OS
sudo dnf install git python3      # Fedora
```

### Getting the Code

```bash
cd ~
git clone https://github.com/dmccuskey/mail-filter.git
cd mail-filter
```

Then continue with [step 2 of the Quick Start](../README.md#2-add-your-account).

### Scheduling

Use cron as in the Quick Start. On a Raspberry Pi with the default `pi` user:

```cron
*/5 * * * * /usr/bin/python3 /home/pi/mail-filter/mail_filter.py >> /home/pi/mail-filter/mail_filter.log 2>&1
```

For a filter that runs for months, set up [Log Rotation](operations.md#log-rotation) so `mail_filter.log` doesn't grow without limit. If you keep passwords in environment variables, the cron line must load them; see [Passwords from Environment Variables](operations.md#passwords-from-environment-variables).

A `python3-json5` package installed with apt is not needed, and not used: Mail Filter always uses its bundled copy.

## Windows

> **Not yet tested.** These steps have not been checked on a Windows machine yet. Please [open an issue](https://github.com/dmccuskey/mail-filter/issues) if something here doesn't work.

### Python and Git

Install Python from [python.org](https://www.python.org/downloads/windows/); its installer includes the `py` launcher, which the commands below use. Install Git from [git-scm.com](https://git-scm.com/download/win). Check both in a new PowerShell or Command Prompt window:

```bat
py --version
git --version
```

### Getting the Code

```bat
cd %USERPROFILE%
git clone https://github.com/dmccuskey/mail-filter.git
cd mail-filter
```

(In PowerShell, use `cd $HOME` for the first line.)

Follow the Quick Start from [step 2](../README.md#2-add-your-account), with these differences:

- Use `py` instead of `python3`: `py list_folders.py my-mail`.
- Run the filter as `py -X utf8 mail_filter.py`. The log uses characters such as `→` and the subjects of your mail, which Windows cannot always write to a file in its default encoding; `-X utf8` makes Python use UTF-8.
- Skip `chmod`; files in your user folder are private to your account by default.

### Scheduling with Task Scheduler

Find the full path of `py`:

```bat
where py
```

Then create a task that runs every 5 minutes, replacing both paths with yours:

```bat
schtasks /Create /TN "mail-filter" /SC MINUTE /MO 5 /TR "cmd /c cd /d C:\Users\you\mail-filter && C:\Users\you\AppData\Local\Programs\Python\Launcher\py.exe -X utf8 mail_filter.py >> mail_filter.log 2>&1"
```

The task runs while you are signed in. To change that, or to stop it, open Task Scheduler and find `mail-filter`. To remove it:

```bat
schtasks /Delete /TN "mail-filter"
```

## Optional: a Virtual Environment

Mail Filter doesn't need one. If you run it in a virtual environment anyway, a scheduled job doesn't activate the environment: point the job at the environment's own Python instead, for example `/home/you/mail-filter/.venv/bin/python` (or `.venv\Scripts\python.exe` on Windows). That Python uses the environment automatically. `.venv/` is already ignored by Git.

## Updating

```bash
cd ~/mail-filter
git pull
```

Your `.local.json5` files are ignored by Git, so updates never change them. The filter checks its configuration each time it starts; if an update needs a configuration change, the log says what is wrong and no mail is processed until it is fixed.
