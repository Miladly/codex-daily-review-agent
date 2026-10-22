# Codex Daily Review Agent

把分散在 Codex 本机与 SSH 服务器上的工作会话，整理为“工作记录为主、复盘思考为辅”的本地 Markdown 日报；用 Obsidian 或其他编辑器阅读、补充反思，按需发布到 Notion。

**本地优先 · 默认无模型调用 · 默认不写 Notion · 不执行会话里的命令**

> This repository contains the Codex-only edition: a local-first, read-only session collector with evidence-linked daily drafts, optional SSH sources, and approval-gated Notion synchronization.

## 能做什么

- 扫描 `CODEX_HOME/sessions` 与 `archived_sessions`，按事件时间处理跨天延续会话。
- 使用字节游标、完整行边界、前缀/尾部指纹和 SQLite 事务恢复增量读取；替换、截断及归档重读时去重。
- 按真实 cwd 和 Git 公共目录归并工作区，支持 worktree、符号路径及本地 file URI。
- 生成“会话 → 项目 → 日报”三级 JSON、Markdown 和证据索引，保留人工补充。
- Markdown 可独立输出到 Obsidian 笔记库；JSON、SQLite、游标与配置留在私有状态目录。
- 提供先预览、后校验复制的笔记迁移；保护同名个人笔记、人工反思与原文件。
- 区分 A（可观察工具记录）、B（会话陈述）、C（意图/计划）；退出码 0 不等于科研结论已验证。
- 可选只读 SSH 采集：服务器内存解析，仅回传脱敏事件和游标；不安装远程文件或服务。
- 统一“工作概览 → 分项目工作记录与产物 → 复盘思考 → 后续事项 → 人工补充”，保留后续周报、月报可复用的日期与项目记录；见 [格式说明](docs/journal-format.md)。
- 内置参考 gstack `/retro` 的复盘方法：全天成果选材、关键决定、近三天延续线索；可选模型提炼要求可核验的下一步完成标准。
- 可选 Codex 非交互式结构化总结，以及经过具体草稿审批的 Notion 同步。

## 快速开始

支持 **macOS、Linux，Python 3.10+**；Windows 用户请使用 WSL2，暂不支持原生 Windows。核心运行和测试仅使用标准库；SSH、Git、Codex CLI 按需要提供。无需 API Key、Notion 账户或 Codex CLI 即可生成规则摘要，前提是已有 Codex 会话日志。

```sh
git clone https://github.com/Miladly/codex-daily-review-agent.git
cd codex-daily-review-agent
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m daily_review init --reports-dir "~/Documents/DailyReview"
python -m daily_review doctor
python -m daily_review daily --through today --days 3
```

当前仓库仍为私有，下载需要仓库访问权限；公开后同一安装流程可用。也可下载 ZIP，解压并进入目录后从创建虚拟环境一步开始。

`init` 生成权限为 0600 的 `config.local.json`，不会覆盖已有配置。`--reports-dir` 指定 Markdown 文件夹；省略时沿用 `var/reports`。默认来源为 `CODEX_HOME` 或 `~/.codex`；非标准位置可用 `--codex-home PATH`，时区可用 `--timezone UTC`。请查看生成的配置，填写自己的排除目录。**程序、私有状态与笔记输出目录默认排除；若在别处运行 Agent，应额外填写相应工作目录和会话 ID。**

日志目录缺失时 `doctor` 与 `daily` 返回退出码 2，提示修正路径或先创建 Codex 会话，不会声称“没有工作”。Codex CLI 缺失只影响可选模型后端，不影响规则摘要。

上述示例的 Markdown 位于 `~/Documents/DailyReview`。在 Obsidian 仓库管理器选择 **Open folder as vault / 打开文件夹作为仓库**，打开该文件夹即可，不需要插件或每天导入。其他编辑器也可直接打开。

完整操作见 [简明手册](docs/usage.md)、[Obsidian 与迁移](docs/obsidian.md)。下载项目不会自动创建定时任务；需要自行按 [定时运行指南](docs/scheduling.md) 配置。

以下路径以配置文件旁的 `var/` 为私有状态根目录（可用 `state_dir` 调整）：

| 文件 | 用途 |
|---|---|
| `reports/YYYY-MM-DD.md` | 未设置 `reports_dir` 时的日报位置；设置后写到指定笔记目录 |
| `reports/YYYY-MM-DD.json` | 三级结构、证据定位和候选建议 |
| `reports/manual/YYYY-MM-DD.md` | 与 Markdown 一起保存；外部笔记目录中为 `manual/YYYY-MM-DD.md` |
| `review.sqlite3` | 本地脱敏事件、游标和同步状态 |
| `runs/last-run.json` | 来源状态、计数和产物路径 |
| `notion-plans/*.dry-run.json` | 离线同步预览，无远程写入 |
| `migrations/` | 执行迁移时的私有配置备份和哈希清单 |

`var/` 和外部笔记目录都包含私密数据，**不能跟源码一起上传或分享**。仓库的 `var/` 忽略规则不保护其他位置的笔记，请检查自己的 Git/云同步设置。自动脱敏不能保证任意自然语言均不含隐私。

## 已有日报迁到 Obsidian

```sh
# 默认仅预览，不写文件或修改配置
python -m daily_review migrate-reports --to "~/Documents/DailyReview"
# 核对预览后复制、校验，并切换活动输出目录
python -m daily_review migrate-reports --to "~/Documents/DailyReview" --apply
```

原 Markdown 保留为快照，配置备份和清单留在私有状态目录。不会迁移 JSON/SQLite，也不会覆盖不同内容的同名文件。迁移后只在新笔记目录编辑，详细冲突和回退说明见 [迁移指南](docs/obsidian.md)。

个人内容请写在日报末尾 **“人工补充与最终反思”** 下方；自动摘要区重跑会更新。不要删除管理标记和反思标题。若已有个人日记占用了同名文件，程序会报错保留，请选用专用子目录或先保存并更名原笔记。

## 常用操作

```sh
# 仅读取新增记录
python3 -m daily_review collect
# 重读并去重（不删除历史证据）
python3 -m daily_review collect --replay
# 仅根据本地 SQLite 生成指定日期草稿
python3 -m daily_review review --date 2026-01-15
# 导出有版本号的本地事件接口（仍是私密数据）
python3 -m daily_review export-events --date 2026-01-15
# 无网络测试
python3 -m unittest discover -s tests -v
```

命令退出码：0=成功；1=配置/运行错误；2=需关注（来源缺失、不完整或模型失败）。诊断输出包含本机路径，请勿原样上传 Issue。

独立 wheel 安装后请在专用工作目录执行 `daily-review init`；从其他目录运行时使用 `daily-review --config PATH ...` 或设置 `DAILY_REVIEW_CONFIG`。相对配置路径始终以配置文件所在目录解释，运行数据不会写入 Python 安装目录。

每日运行建议使用你自己的调度工具调用 `daily --through today --days 3`。它会回补最近三天；来源不可用时保留已有事件并返回不完整状态，不能将缺失记录解释为“没有工作”。例如自行设为 23:30，则当次是截至执行时的草稿，之后发生的工作会在下次补回原日期。本仓库不安装后台服务，不提供准点唤醒保证。休眠、网络及调度权限需要由运行环境处理，见 [定时运行指南](docs/scheduling.md)。

## 可选功能

**SSH**：在本地私有配置的 `remote_sources` 中添加 SSH 别名。预先自行确认主机密钥和非交互认证，绝不关闭主机密钥校验。macOS 可复用 Keychain。配置和权限见 [配置说明](docs/configuration.md)。

**模型总结**：`model-preview` 先导出将要发送的有限脱敏片段；审阅后可用 `review --backend codex`。这会把片段发送给你所配置的模型提供方，`daily` 默认不会调用模型。模型工具活动、无效证据引用和证据升级会被拒绝。模型会明确加载内置 skill 与质量量表，先保留项目与会话层的工作记录、产物和阶段结果，再提炼少量决定、问题、下一步和跨天延续；历史线索不会自动算作当日完成。规则后端按全天信号选材，仍是保守摘录。来源、许可证、适配范围和验证边界见 [复盘方法依据](docs/review-method-sources.md)。

**Notion**：复用你已有的工作记录和项目库，配置自己的 data source ID。令牌只从 `NOTION_TOKEN` 或 macOS Keychain 服务 `DailyReviewAgent.Notion` 读取。先生成 `notion-plan --online`，检查内容后用 `notion-sync --plan ... --approve ...` 执行。只更新 Agent 管理块；人工改动、已确认记录、重复日期/类型行等会阻止写入。不会自动修改 schema 或创建任务/知识；缺失项目补建需要显式配置和同次审批。

## 分享前检查

源码和合成示例可以公开，真实日报、原始会话、SQLite、个人映射、SSH 配置、Notion ID 与 Token 均应保留本地。本项目采用发布文件白名单与暂存区隐私检查：

```sh
python3 tools/check_release.py
```

检查工具不会打印发现的秘密值。它是辅助检查，不能替代对暂存文件的人工审阅。安全边界和威胁模型见 [PRIVACY.md](PRIVACY.md)，架构与测试范围见 [架构说明](docs/architecture.md)。

本项目基于观察到的 Codex JSONL 结构；该结构不是稳定公开协议。升级 Codex 后应先用 `doctor` 和小范围样本验证。日志缺失、截断、工具未记录结果或摘要选取上限都会限制覆盖范围。规则摘要是证据摘录，不替代个人反思。

## License

MIT，见 [LICENSE](LICENSE)；上游方法致谢见 [第三方声明](THIRD_PARTY_NOTICES.md)。示例为合成内容，不包含真实用户会话。贡献与反馈见 [CONTRIBUTING.md](CONTRIBUTING.md)，漏洞处理见 [SECURITY.md](SECURITY.md)，验收范围见 [发布就绪说明](docs/release-readiness.md)。
