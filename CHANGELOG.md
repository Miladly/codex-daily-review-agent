# Changelog

## 0.3.0

- Add separate Markdown/Obsidian output with `reports_dir` and `init --reports-dir`; retain JSON, SQLite, cursors and plans in private state.
- Add notes-directory self-exclusion, unmanaged/damaged same-name note protection and symlink checks while preserving manual reflections.
- Add offline preview-first `migrate-reports`, explicit verified copy-and-switch, original note retention and private configuration backup/manifest.
- Document a complete local reading workflow, existing-vault conflicts, migration recovery, user-owned scheduling, timezone boundaries and late-event backfill.
- Extend synthetic tests and installed-wheel acceptance coverage for external notes, conflicts, migration interruption and concurrent edits; maintain the Codex-only source boundary.

## 0.2.1

- Add private configuration initialization with no overwrite and explicit missing-source diagnostics.
- Preserve mandatory self/state exclusions when configuration lists are empty.
- Resolve relative paths from the configuration directory; support an explicit config environment variable and installed-wheel use outside a checkout.
- Validate configuration before creating state; reject credentials in JSON.
- Document macOS/Linux support, WSL2 guidance, complete clone/ZIP setup, exit codes and troubleshooting.
- Extend release checks to commit metadata and workspace identifiers; document exact synthetic scanner false positives.
- Add independent download, installation, package-content and privacy audit coverage.

## 0.2.0

- Initial Codex-only local/SSH collection, evidence-linked drafts, conservative summaries and approval-gated Notion sync.
