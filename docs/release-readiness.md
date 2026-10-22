# Release readiness

## Scope

This is a shareable local MVP for macOS/Linux and Python 3.10+, with configuration, tests, MIT licensing, privacy documentation, contribution guidance and CI. It is not a guaranteed unattended scheduler, a stable Codex log-format standard, a native Windows application, or an automatic scientific-validity checker.

Core drafts require only Python and existing Codex session logs. SSH, Git worktree matching, model summarization and Notion are optional capabilities with explicit prerequisites. The repository supplies neither service credentials nor a preconfigured personal workspace.

## Acceptance checks

- Clone/ZIP installation in an isolated environment; configuration initialized without a credential.
- Noninteractive configuration, timezone and relative path validation; no overwrite on repeated init.
- Local drafts and event exchange generated from synthetic sessions; no source changes or network/model calls.
- Repeat ingestion has no new events and stable drafts; a missing source reports attention and later recovers.
- Required self-exclusion survives the example configuration; runtime files use private permissions.
- Wheel and source distribution contain only approved source/docs/tests/metadata, with no runtime database, log, credential or user mapping.
- Complete Git history and metadata checked for secret patterns, workspace IDs, personal paths, email and private network addresses; an additional private exact-identifier comparison is done outside the repository.
- Independent Gitleaks history and source scans, with offline reports and no upload of source to a scanning service.
- External Markdown output with structured state retained privately, additive notes self-exclusion, same-name note/symlink protection and manual reflection preservation.
- Offline migration preview without writes; applied verified copies, original/configuration backup retention, conflicts, interruption/resume and concurrent editing protection.
- Installed-wheel initialization, migration and regeneration in a separate synthetic notes folder; no real Obsidian account, model, server or Notion credential is used.

- Factual records precede review notes in both rule and optional model output; date/project/result metadata survives JSON and SQLite storage.
- Later repair/provisional approval, early delivery under retry noise, model record evidence boundaries and safe recorded artifact navigation use synthetic regression cases.

## Audit policy

The publishable history contains only generic code, documentation and synthetic fixtures. No credential, path or rule exceptions are installed for the independent scanner. Privacy audit reports and personal identifier lists remain outside the repository. Recheck the full history and distribution contents before every public release.

## Limits

Known-format redaction is best effort. Evidence excerpts can still contain project names or proprietary prose. Keep local state and every real report private. The scheduled runner's environment, wake state, authentication and network affect execution. Confirm optional integrations against your own account; the automated suite uses mocks and synthetic data, not live credentials.
