#!/usr/bin/env python3
"""
Run the live IMAP tests against real servers (see docs/development.md#testing).

  python3 imap_tests.py                   every account with test_enabled
  python3 imap_tests.py gmail-main        only the accounts named
  python3 imap_tests.py --keep gmail-main leave test messages and folders in place
  python3 imap_tests.py --record          also save the filter's IMAP replies to
                                          tests/fixtures/imap/ for offline replay
  python3 imap_tests.py --check-recordings  check the recordings for identifying
                                          details (no server is contacted)

The tests create messages marked "[mail-filter testing only]" in each tested
account's INBOX, run the filter on them, and then delete what they created.
"""
import argparse
import os
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "tests"))


def check_recordings():
    """
    Check every recording in tests/fixtures/imap/ for details that identify
    the accounts in accounts.local.json5. Returns True if all are clean.
    """
    import mail_filter
    import imap_recording

    try:
        accounts = mail_filter.load_json(BASE / "accounts.local.json5")
    except (OSError, ValueError) as e:
        print(f"ERROR: cannot read accounts.local.json5: {e}")
        return False
    paths = sorted((BASE / "tests" / "fixtures" / "imap").glob("*.txt"))
    if not paths:
        print("No recordings in tests/fixtures/imap/")
        return True
    clean = True
    for path in paths:
        problems = imap_recording.recording_problems(path, accounts)
        name = path.relative_to(BASE)
        if not problems:
            print(f"Recording OK: {name}")
        for problem in problems:
            print(f"ERROR: {name} {problem}")
        clean = clean and not problems
    if not clean:
        print("ERROR: do not commit these recordings; see "
              "docs/development.md#reviewing-a-recording")
    return clean


def main():
    parser = argparse.ArgumentParser(description="Run the live IMAP tests.")
    parser.add_argument("accounts", nargs="*", metavar="account_id",
                        help="accounts to test (default: every account with test_enabled)")
    parser.add_argument("--keep", action="store_true",
                        help="leave test messages and folders on the server for inspection")
    parser.add_argument("--record", action="store_true",
                        help="save the filter's scrubbed IMAP replies to tests/fixtures/imap/")
    parser.add_argument("--check-recordings", action="store_true",
                        help="only check the recordings for identifying details, then exit")
    args = parser.parse_args()

    if args.check_recordings:
        sys.exit(0 if check_recordings() else 1)

    # The live test module reads these when it is imported
    os.environ["MAILFILTER_LIVE_TESTS"] = "1"
    os.environ["MAILFILTER_LIVE_KEEP"] = "1" if args.keep else ""
    os.environ["MAILFILTER_LIVE_RECORD"] = "1" if args.record else ""
    os.environ["MAILFILTER_LIVE_ACCOUNTS"] = ",".join(args.accounts)

    import test_live_imap as live

    if live.CONFIG_PROBLEMS:
        for problem in live.CONFIG_PROBLEMS:
            print(f"ERROR: {problem}")
        print("ERROR: no live tests run")
        sys.exit(1)

    live.LOG_FILE.write_text("")
    print(f"Live IMAP tests for: {', '.join(live.ACCOUNTS)}")
    print(f"The filter's full log is written to {live.LOG_FILE.name}")
    suite = unittest.defaultTestLoader.loadTestsFromModule(live)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    passed = result.wasSuccessful()
    if args.record:
        print()
        passed = check_recordings() and passed
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
