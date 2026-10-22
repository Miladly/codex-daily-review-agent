# Architecture

```text
local Codex JSONL ──► incremental collector ─┐
                                           ├─► local SQLite ─► sessions ─► projects ─► daily drafts
SSH Codex JSONL ──► remote memory parser ────┘                                         │
                                                                                      └─► offline Notion plan
                                                                                           │ explicit approval
                                                                                           ▼
                                                                                     owned Notion block
```

The local collector checkpoints only complete lines, using device/inode plus prefix/tail fingerprints. A malformed complete line is diagnosed and skipped; an incomplete final line is retried. Cursors and normalized events commit together. Replacement and truncation replay with event identity deduplication. Active and archived directories are scanned without using their directory date to select events.

Events are attributed to the current cwd, resolved through explicit project overrides or Git's common directory. Sessions can contribute to several days and projects. Reports filter timezone-aware event timestamps. A/B/C describe evidence availability rather than scientific validity.

SQLite retains observations and their provenance. Summary claims about the same full Git commit within the same canonical project can combine references. An abbreviated hash is expanded only with a successful read-only Git query; hashes are never fabricated. Two edits to the same path are not automatically the same outcome. This is conservative outcome deduplication, not complete semantic equivalence detection.

SSH runs the trusted parser in isolated Python with bytecode disabled and an in-memory database, returning batches of bounded redacted events. The scan budget is approximately 8 MiB per batch, stopping at line boundaries; an individual source line remains bounded by 16 MiB. Completed batches commit locally; failed batches do not move their cursor. Source outages are recorded independently of retained historical events.

The `daily` entry point collects once and refreshes a configurable day range, using content fingerprints that exclude generation time. No-change drafts remain stable. Manual reflections use a sidecar and are also preserved when edited beneath the reflection heading. Coverage reports count observations; they do not claim a complete account of offline work.

Notion uses date plus record type as its lookup key, fails on duplicate matches, verifies schema and project relation targets, and requires a fresh content-bound approval hash. A sync can resume from a verified local connector receipt. Existing human relations are retained. Confirmed/locked pages and modified managed content block updates. Pending knowledge and task suggestions are never directly created.

The versioned `daily-review.events` JSON exchange is local/private and intentionally distinct from daily summary JSON. It includes event IDs, tool/origin, project IDs, evidence levels, optional observed commit hashes, and source references. It is an output contract, not permission to treat arbitrary imported data as verified evidence.

Tests use temporary directories and synthetic JSONL. Coverage includes archive moves, mirrored sessions, cursor crashes, torn/oversized records, timezone boundaries, worktrees, self-exclusion, remote memory isolation, transport failures, read-only source invariance, model reference validation, and Notion approval/human-content protection. They do not require real credentials or contact live services.
