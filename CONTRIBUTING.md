# Contributing

Use Python 3.10+ on macOS or Linux. Create a virtual environment and install editable source as described in the README.

```sh
python -m unittest discover -s tests -v
python tools/check_release.py --history
```

Use synthetic JSONL in temporary directories. Tests must not read personal Codex data, call a live model, contact Notion, execute logged commands, or require real SSH credentials. Runtime data, even redacted data, must stay outside commits.

Before a pull request, inspect `git diff --cached`, run the release check, and describe the problem, behavior change and tests. If adding a publishable file, update the explicit allowlist in `tools/check_release.py`. Do not broadly ignore security scanner findings; investigate and, for a proven synthetic false positive, use a documented exact fingerprint only.

For bug reports, provide OS/Python versions, package version, redacted error category, and a minimal synthetic reproduction. Never attach actual JSONL, SQLite, complete reports, configuration, SSH files, API keys, or screenshots containing private data. Diagnostics can contain paths and IDs. Use SECURITY.md for suspected vulnerabilities.
