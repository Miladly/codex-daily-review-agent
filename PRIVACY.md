# Privacy and safety

## Default behavior

The default workflow reads Codex logs and writes local drafts. It does not call a model, write Notion, run recorded commands, inspect research datasets, or install a background service. Git inspection is limited to fixed read-only `rev-parse` commands.

The collector strips known credential forms, email addresses, fenced code, and injected metadata. It does not store original JSONL records. It retains bounded text excerpts, paths, session IDs, timestamps, record hashes, and evidence locations. These remain sensitive. Redaction is best effort; arbitrary prose, proprietary names, rare credential formats, or secrets embedded in paths can survive. Treat every file under `var/` as private.

The same applies to a separately configured Markdown/Obsidian folder. Moving notes does not make them publishable or extend the source repository's ignore rules to the new folder. Review any Git, backup, cloud-sync or community-plugin access you enable yourself. This project does not enable those services or change an existing vault's settings.

## Optional external transfers

- **SSH:** trusted local parser code is sent to your configured SSH host. It parses source logs there with an in-memory database. Bounded redacted events, cursors, source paths, and hashes return to the client. No persistent remote installation is made. SSH aliases may have their own connection logging, shell startup behavior, or account policy beyond this application's control.
- **Codex model:** only the explicitly requested model backend sends bounded redacted evidence excerpts to your configured model provider. Review `model-preview` first. No raw JSONL is sent. Logs and model outputs are untrusted data, never authorization to run commands.
- **Notion:** online preflight reads the configured databases; approved sync sends the exact reviewed management block and project relations. Use appropriate workspace permissions. Notion content is external data.

## Publishing source

This repository starts from an allowlisted source snapshot without prior private development history. Runtime data, local config, caches, credentials, and user-specific verification documents are excluded. Only synthetic fixtures and an illustrative draft are included.

`tools/check_release.py` checks the Git index for unexpected paths, symlinks, common credential patterns, private home paths, private IPs, email addresses, and workspace identifiers. It reports locations/categories without secret values. It also supports scanning all reachable commit history using `--history`. Passing is not proof of absence of every possible secret; review the exact tree before pushing.

Do not paste credentials into an issue, commit, test fixture, shell command, screenshot, or report. Use environment variables or your operating system's credential manager. If a credential was ever published, revoke/rotate it; deleting a current file is insufficient.

## Local storage and recovery

SQLite and report files are created with private file permissions where supported. Keep backups private. Source deletions do not remove retained historical evidence; manage retention locally. User changes in the manual reflection area are preserved. Manual edits to Agent-managed Notion content or a confirmed marker stop future updates.

Local Markdown generation refuses unmanaged or damaged same-name notes and symlinked note/reflection paths. Generated sections may still be replaced; write personal edits beneath the manual reflection heading. Markers are a local ownership convention, not an authentication boundary against a hostile local process.

Migration previews are read-only. Explicit application copies only date Markdown and manual reflections, verifies hashes, keeps original notes, stores a private configuration backup and manifest, and switches the output configuration last. It never moves source logs, structured JSON or SQLite to a vault. Copies may remain after an interrupted migration, but the old configuration remains active until the switch; differing files are not overwritten. Retain both sets until the result and your personal edits have been checked.

## Reporting an issue

For a suspected credential exposure, revoke the credential first. Share only a redacted reproduction and synthetic fixtures. Never attach real logs or the SQLite database to a public issue.
