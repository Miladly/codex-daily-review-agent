# Review method: sources and adaptation

Research snapshot: 2026-10-22. Star counts were read from GitHub's public repository API. They describe entire repositories, not usage, quality or stars of an individual skill. No third-party skill was installed or executed.

| Upstream | Repository stars | License | Relevance and decision |
| --- | ---: | --- | --- |
| [garrytan/gstack](https://github.com/garrytan/gstack) | 137,244 | MIT | Primary reference: `/retro` reconstructs engineering outcomes, compares periods and checks follow-through on earlier recommendations. Adapt these ideas to bounded daily evidence. |
| [ComposioHQ/awesome-claude-skills](https://github.com/ComposioHQ/awesome-claude-skills/tree/master/developer-growth-analysis) | 77,644 | Not confirmed during review | Developer-growth analysis of chat history; includes learning-resource lookup and Slack delivery. Not adopted: different log assumptions, broader external access, unclear license status. |
| [kepano/obsidian-skills](https://github.com/kepano/obsidian-skills) | 49,903 | MIT | Format reference for Obsidian Markdown. This is not a daily retrospective method; no source text copied. |
| [zhouzilai626/codex-retro-studio](https://github.com/zhouzilai626/codex-retro-studio) | 1 | MIT | Close topical match with daily/weekly/monthly Codex retrospectives and presentation. Useful comparison, insufficient evidence to call it widely adopted. No source copied. |

## Primary source pin

Reviewed [gstack retro template](https://github.com/garrytan/gstack/blob/514fbfdc9799376edfd2536f60659501a48ead64/retro/SKILL.md.tmpl), commit `514fbfdc9799376edfd2536f60659501a48ead64`, blob `156ecd7df499906c8fd273be01b5d5d5d693f13c`. The upstream [MIT license](https://github.com/garrytan/gstack/blob/514fbfdc9799376edfd2536f60659501a48ead64/LICENSE) is retained in [third-party notices](../THIRD_PARTY_NOTICES.md). The packaged instructions are a project-specific adaptation, not the original `/retro` implementation.

Retain: evidence-backed results, meaningful changes rather than activity counts, previous-context comparison, and evidence for whether a prior recommendation was followed. Our method asks for goals, progress and verification limits, decisions, blockers, next actions with acceptance criteria, and dated carryover.

Do not import gstack's bootstrap, telemetry, scripts, dependencies, team contribution ranking, or estimates of effort from commits/timestamps/lines changed. Session logs cover research, writing and operations too; Git activity alone is insufficient. Our existing read-only collector, source coverage, A/B/C evidence constraints, privacy filtering and human reflection remain the execution boundary.

Historical carryover is a *candidate* from the preceding three days, not a complete task ledger or an automatic completion detector. Repeated topic names do not prove an earlier action was completed. The optional model must cite today's evidence when connecting it to a result.

## Integration and evaluation

The canonical skill is `daily_review/skills/evidence-daily-review/SKILL.md`; the repository discovery entry links to it. The optional `review --backend codex` adapter explicitly loads the packaged instructions and rubric. Merely putting a skill in a folder would not guarantee that the isolated summarizer used it.

Rules-only nightly runs use deterministic whole-day selection and dated prior context; they do not run the skill through a model. The model adapter still requires explicit use. No extra data source, external destination or automatic publication is enabled by the skill.

Offline cases test early outcomes under late retry noise, intent/outcome boundaries, project budget sharing, dated history, references and actionable criteria. Passing those checks proves the tested mechanics, not universal semantic accuracy or improved real-work review quality. Real draft quality still needs human review.
