# Configuration

Run `python -m daily_review init` from a dedicated project directory, or copy `config.example.json` to `config.local.json`. Init creates a private file and refuses to overwrite it. Configuration lookup order is explicit `--config`, `DAILY_REVIEW_CONFIG`, current directory's `config.local.json`, then package-root `config.local.json`. Use only configuration you trust.

The default Codex source is `CODEX_HOME`, falling back to `~/.codex`. Paths in configuration resolve relative to that configuration file (including project overrides), with `~` expansion. State defaults to the configuration directory's `var/`, or the source checkout's `var/` when no configuration exists. An installed wheel without configuration uses a private per-user data directory; it never writes into site-packages. State must be outside the source directory. Required exclusions for the package and state directory are additive, even if `exclude_cwds` is empty.

Supported platforms are macOS and Linux, Python 3.10+ with IANA timezone data. Native Windows is unsupported; use WSL2. Git and SSH are optional until their features are configured. The Codex CLI is optional until model summarization is explicitly requested.

The default timezone is `Asia/Shanghai`; set your own IANA timezone if desired. Exclude all directories and session IDs used by the reviewing agent. `CODEX_THREAD_ID`, when present, is excluded automatically; init also persists it for later runs. Keep exclusions in private configuration, not public examples.

Optional `reports_dir` controls Markdown and its `manual/` reflection folder only. It defaults to `state_dir/reports`, preserving existing installations. `init --reports-dir PATH` configures it for a new installation; use `migrate-reports --to PATH` then `--apply` for existing notes. Relative paths resolve from the configuration directory. JSON, SQLite, cursors and plans stay under `state_dir`. A notes folder cannot overlap `CODEX_HOME` or contain private state. The notes directory is an additive self-exclusion. See [Obsidian and migration](obsidian.md).

```json
{
  "timezone": "Asia/Shanghai",
  "reports_dir": "~/Documents/DailyReview",
  "exclude_session_ids": [],
  "exclude_cwds": ["~/projects/my-review-agent"],
  "project_overrides": [
    {"cwd": "~/projects/example", "key": "example", "name": "Example project"}
  ],
  "remote_sources": [
    {"id": "research", "label": "Research host", "host": "my-ssh-alias", "timeout": 90}
  ],
  "notion": {
    "records_data_source": "",
    "projects_data_source": "",
    "project_aliases": {},
    "create_missing_projects": false
  }
}
```

SSH uses your existing host configuration, strict known-host verification, batch authentication, no port forwarding, and no local SSH commands. Confirm the exact alias and port yourself first. A configured remote `codex_home` overrides remote `CODEX_HOME`; otherwise the server's environment/default is used. Shell startup environment may differ from an interactive login. Remote Python 3.10+ and Git are needed for matching capabilities. macOS uses existing SSH Keychain support; no credentials are saved by this project.

Notion expects the existing fields `记录` (title), `日期` (date), `记录类型` (select with `每日记录`), and `项目` (relation to a project data source whose title field is `项目`). Optional `状态`/`复盘状态=已确认` or the literal block marker `DAILY_REVIEW_CONFIRMED` protect a page. No schema changes are made. Fill in your own data source IDs and keep them private.

Provide `NOTION_TOKEN` through your environment or use a Keychain item named `DailyReviewAgent.Notion`. Do not put credentials in JSON. `project_aliases` can map a stable local project ID or exact project name to an existing Notion page ID. Missing-project creation defaults off and, when enabled, occurs only within an explicitly approved synchronization. Ambiguous or stale mappings stop creation.

Example approval sequence:

```sh
python3 -m daily_review notion-plan --date 2026-01-15 --online
# Inspect the generated local plan and exact content before approving.
python3 -m daily_review notion-sync --plan PATH_TO_REVIEWED_PLAN --approve APPROVAL_HASH
```

The approval hash detects content changes; it is not a substitute for human review or a server-side authorization system. Do not automatically copy a freshly generated hash into a sync command.


## Troubleshooting

- Exit 1 without a traceback: validate JSON object/list types, the timezone, source paths, and remote limits. Unknown configuration fields and credential fields are rejected before state is created.
- Exit 2 with missing local source: set `codex_home` to the directory containing `sessions` or `archived_sessions`; creating your first Codex session may be required. A healthy but empty directory is different from a missing source.
- Remote source unavailable: verify the exact SSH alias/port and known host first; use noninteractive credentials. Retained events remain available and reports state that coverage may be incomplete.
- No `codex` binary: rules-based daily drafts still work. Only the explicit model backend needs the CLI and its own authentication/provider configuration.
- Scheduling: invoke with absolute Python, config, and working-directory paths. A shell environment variable from an interactive terminal may not be present in a scheduler. No scheduler is installed by this repository.
- Existing date note refused: keep your personal note and choose a dedicated output folder or rename it after backing up. Agent markers and the manual reflection heading must remain intact. Do not use force-overwrite workarounds.
- Migration interrupted: original notes and the old configuration remain active until a verified switch. Identical copies can be reused by retrying; conflicting copies require human review. File systems that do not support the no-overwrite hard-link installation fail without a configuration switch.
- Notion: default offline plans need no credential. Online access requires your own schema and IDs; see the preflight error before changing anything. Do not paste tokens or full diagnostic output into an issue.


Optional `codex_binary` is either an executable name on PATH or a path relative to the configuration directory (with tilde expansion); use an absolute path for a scheduler. `model`, when set, must be a nonempty string. `doctor --backend codex` checks the executable/interface without calling a model or verifying authentication. See [first-use troubleshooting](troubleshooting.md).
