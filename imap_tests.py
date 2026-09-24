#!/usr/bin/env python3
"""
Run the live IMAP tests against real servers (see docs/development.md#testing).

  python3 imap_tests.py                   every account with test_enabled
  python3 imap_tests.py gmail-main        only the accounts named
  python3 imap_tests.py --keep gmail-main leave test messages and folders in place

The tests create messages marked "[mail-filter testing only]" in each tested
account's INBOX, run the filter on them, and then delete what they created.
"""
import argparse
import os
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description="Run the live IMAP tests.")
    parser.add_argument("accounts", nargs="*", metavar="account_id",
                        help="accounts to test (default: every account with test_enabled)")
    parser.add_argument("--keep", action="store_true",
                        help="leave test messages and folders on the server for inspection")
    args = parser.parse_args()

    # The live test module reads these when it is imported
    os.environ["MAILFILTER_LIVE_TESTS"] = "1"
    os.environ["MAILFILTER_LIVE_KEEP"] = "1" if args.keep else ""
    os.environ["MAILFILTER_LIVE_ACCOUNTS"] = ",".join(args.accounts)

    sys.path.insert(0, str(BASE / "tests"))
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
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
