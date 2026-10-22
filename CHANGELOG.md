# Changelog

## 0.6.0

- Add explicit `daily --backend codex` for scheduled model-authored work journals and review notes; rules remain the default.
- Reuse revalidated exact-input model answers across three-day backfills; invalidate on evidence, source completeness, method or configured model changes.
- Persist factual drafts before calls; mark model failures, continue other dates and preserve manual reflection. Return metadata without private report content.
- Document one-off versus standing authorization, model service data boundaries, cache privacy and fallback behavior.


## 0.5.0

- Make factual work records the main body, followed by separate review thoughts, next actions, manual additions and source detail.
- Add stable date/project/session record metadata, recorded result/status and artifact navigation for later weekly/monthly aggregation; keep rule records when optional model records enrich the page.
- Prioritize both early delivery and the latest recorded session state, with chronological presentation and bounded excerpts; historical/intent-only evidence cannot become today's model work record.
- Update the packaged skill to 1.1.0, its rubric, scheduling guide and synthetic journal example. Default daily runs remain model-free and do not publish notes.
- Preserve existing manual content, privacy checks, Codex-only release scope and offline installed-wheel validation.

## 0.4.0

- Add a packaged, versioned evidence-daily-review skill and quality rubric, informed by gstack `/retro`; retain pinned provenance and MIT notices.
- Select evidence from the whole day with repeated-noise limits and project/session rotation for bounded model input; disclose omitted candidates.
- Add dated, bounded three-day prior context without counting it as today's work.
- Explicitly load the skill for the optional model adapter; validate review sections, evidence scopes, prior-only and intent-only outcome boundaries, and next-action acceptance criteria.
- Keep nightly `daily` rules-only; no new external calls, source access or publication permissions.
- Add synthetic quality regression cases and installed-resource acceptance checks. Semantic accuracy still requires human review.

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
