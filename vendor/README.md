# Bundled Packages

Third-party code bundled with Mail Filter, so it runs on a plain Python 3 install with nothing to `pip install`.

## json5

- **Source:** [dpranke/pyjson5](https://github.com/dpranke/pyjson5), from the `json5` package on PyPI
- **Version:** 0.15.0
- **License:** Apache License 2.0, in [json5/LICENSE](json5/LICENSE). Copyright 2015 Google Inc.
- **Files:** `__init__.py`, `lib.py`, `parser.py`, and `version.py`, copied unmodified. The package's command-line tool (`tool.py`, `host.py`, `__main__.py`) is left out; the parser does not use it.

`mail_filter.py` puts this directory at the front of Python's module search path, so the bundled copy is used even when a `json5` package is installed. `list_folders.py` and the tests load config through `mail_filter.load_json()`.

### Updating

1. Download the new release: `pip download json5==<version> --no-deps`
2. Unzip the `.whl` file (it is a zip archive).
3. Copy the four files above from its `json5/` directory into `vendor/json5/`, replacing the old ones, and copy `json5-<version>.dist-info/licenses/LICENSE` to `vendor/json5/LICENSE`.
4. Update the version in this file.
5. Run the unit tests: `python3 -m unittest discover tests`
