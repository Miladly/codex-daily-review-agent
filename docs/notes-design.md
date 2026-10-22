# Local Markdown output design

## Approved scope

Codex logs are the only source adapter in this edition. The primary reading workflow is a local Markdown editor or Obsidian; optional Notion publication remains approval-gated. All examples and tests are synthetic. No personal paths, server configuration, workspace IDs, real reports or private adapters belong in the release.

## Implementation plan and boundaries

1. Add optional `reports_dir` and `init --reports-dir`, resolving relative paths from the private configuration. Default to `state_dir/reports` for compatibility. Reject overlap with Codex source logs or a notes root containing private state. Make the notes workspace an additive self-exclusion.
2. Write Markdown and manual reflections to the notes folder; retain structured JSON, SQLite, cursors, plans and audits in private state. Refuse symlinked files, unmanaged same-date notes and damaged ownership/reflection markers. Edits inside the generated section are still replaced; the reflection area is the supported human editing surface.
3. Provide offline `migrate-reports --to PATH`, preview-only by default. `--apply` holds the same state lock, copies recognized date notes and reflections without overwrite, verifies hashes, saves a private configuration backup and manifest, then atomically switches the configuration. Original notes remain as a historical snapshot. On interruption before the switch, the old configuration remains active; identical partial copies can be reused. Differing files, concurrent edits, overlapping folders and symlinks stop the operation.
4. Document direct Obsidian opening, explicit user-owned scheduling, timezone/cross-day behavior, optional model and Notion steps, conflicts, backup and rollback. Do not install a scheduler, modify Obsidian global settings or change an existing vault's Git/cloud-sync settings.
5. Verify unit scenarios, installed-wheel use outside the checkout, source/wheel/sdist contents, the full Git history and independent secret scanning before publication.

## Limits

The migration is a verified copy and active-output switch, not a bidirectional sync engine. It does not delete originals or automatically move them again. It uses same-directory temporary files plus hard links for no-overwrite installation; unsupported filesystems fail without switching configuration. No guarantee is made against a hostile local process replacing paths during a filesystem operation. A scheduler must provide wake state, access to private paths and remote connectivity; Obsidian itself is a reader/editor, not the scheduler.
