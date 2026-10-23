# Open-source readiness audit

## Release position

Version 0.7.1 is a local CLI **Beta**, suitable for users who can configure Python and review private local output. It is not a 1.0 consumer application or an always-on scheduling service. Opening the source is separate from publishing personal journals or changing repository visibility.

## User journey findings and fixes

| New-user scenario | Previous failure | Current behavior and regression coverage |
|---|---|---|
| Initialize in an Agent chat, run later elsewhere | Current chat exclusion was only temporary | init persists the current thread ID; required directory exclusions remain additive |
| Mistype a date or timeout | Collection and SQLite creation could precede validation | CLI validates dates, day ranges and timeout before state creation or collection |
| Place config after a subcommand | Ordinary argument ordering was rejected | Both documented positions work without discarding the global config value |
| Use model mode from a different working directory | A relative CLI path followed the process directory | codex_binary resolves from the config directory; malformed model configuration is rejected |
| Check a model installation | Basic doctor could say ok while no model CLI was available | doctor --backend codex requires the interface; timeout becomes a diagnostic; authentication and network remain explicitly untested |
| Wait through multiple dates | No indication whether collection or model generation was active | Metadata-only progress identifies the phase/date; stdout remains machine-readable; quiet is optional |
| Recover from a model timeout | Single-date notes could omit the failure warning | Markdown states that model review is incomplete; facts and human reflection remain available |
| Check yesterday after a missed/interrupted run | An old file or older successful run was easy to mistake for current success | Read-only status checks expected date/backend, note existence and unfinished-run marker |
| Open damaged or future SQLite state | Raw errors or a future schema version could be reset to the known version | No automatic reset/downgrade; clear private-backup and compatible-version guidance |

Tests use synthetic logs in temporary directories. Installed-wheel acceptance also uses a local executable simulator to verify model-adapter success, exact-input reuse, failure, recovery and status. It makes no live model, SSH or Notion request. Real private evidence is never a test fixture or package input.

## Remaining release boundaries

- Python CLI setup and the user's scheduler remain prerequisites. No background service is installed; sleep, application shutdown and permissions can delay execution. Read-only health checks detect completed or missing runs when invoked.
- The optional model adapter has been checked locally with codex-cli 0.163.0-alpha.11.1; other releases must pass doctor interface checks.
- The observed Codex JSONL format and optional CLI flags are not controlled by this project. Diagnostics and bounded synthetic regression tests reduce compatibility risks but cannot promise support for every release.
- Input selection is bounded. Summaries can omit work; redaction and semantic correctness need human judgment. Artifact references do not verify that files exist or scientific conclusions are valid.
- Remote and Notion integrations depend on the user's account/network/schema. Automated tests simulate them. Passing offline tests does not certify every user's live integrations.
- macOS/Linux are supported; native Windows is not. CI includes Linux on Python 3.10/3.13 and macOS on Python 3.13. Other combinations are not claimed as verified.
- Before public visibility: rerun full source/history/package privacy checks, review exact tracked files and metadata, verify CI, and keep runtime directories/configuration separate. Never expose personal verification artifacts.

See [first-use troubleshooting](troubleshooting.md), [scheduling](scheduling.md), [release checks](release-readiness.md) and [privacy](../PRIVACY.md).


Final review additionally verified the managed Markdown display boundary: log/model text is escaped, and percent-encoded or persisted artifact targets cannot bypass protocol/control-character validation. Human reflection remains untouched.
