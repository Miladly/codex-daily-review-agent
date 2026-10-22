# Codex Daily Review Agent

把分散在 Codex 本机与 SSH 服务器上的工作会话，整理为可追溯、待人工审核的每日复盘。

**本地优先 · 默认无模型调用 · 默认不写 Notion · 不执行会话里的命令**

> This repository contains the Codex-only edition: a local-first, read-only session collector with evidence-linked daily drafts, optional SSH sources, and approval-gated Notion synchronization.

## 能做什么

- 扫描 `CODEX_HOME/sessions` 与 `archived_sessions`，按事件时间处理跨天延续会话。
- 使用字节游标、完整行边界、前缀/尾部指纹和 SQLite 事务恢复增量读取；替换、截断及归档重读时去重。
- 按真实 cwd 和 Git 公共目录归并工作区，支持 worktree、符号路径及本地 file URI。
- 生成“会话 → 项目 → 日报”三级 JSON、Markdown 和证据索引，保留人工补充。
- 区分 A（可观察工具记录）、B（会话陈述）、C（意图/计划）；退出码 0 不等于科研结论已验证。
- 可选只读 SSH 采集：服务器内存解析，仅回传脱敏事件和游标；不安装远程文件或服务。
- 可选 Codex 非交互式结构化总结，以及经过具体草稿审批的 Notion 同步。

## 快速开始

支持 **macOS、Linux，Python 3.10+**；Windows 用户请使用 WSL2，暂不支持原生 Windows。核心运行和测试仅使用标准库；SSH、Git、Codex CLI 按需要提供。无需 API Key、Notion 账户或 Codex CLI 即可生成规则摘要，前提是已有 Codex 会话日志。

```sh
git clone https://github.com/Miladly/codex-daily-review-agent.git
cd codex-daily-review-agent
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m daily_review init
python -m daily_review doctor
python -m daily_review daily --through today --days 3
```

当前仓库仍为私有，下载需要仓库访问权限；公开后同一安装流程可用。也可下载 ZIP，解压并进入目录后从创建虚拟环境一步开始。

`init` 生成权限为 0600 的 `config.local.json`，不会覆盖已有配置。默认来源为 `CODEX_HOME` 或 `~/.codex`；非标准位置可运行 `python -m daily_review init --codex-home PATH --timezone UTC`。请查看生成的配置，填写自己的排除目录。**复盘项目目录默认排除；若在别处运行 Agent，应额外填写相应工作目录和会话 ID。**

日志目录缺失时 `doctor` 与 `daily` 返回退出码 2，提示修正路径或先创建 Codex 会话，不会声称“没有工作”。Codex CLI 缺失只影响可选模型后端，不影响规则摘要。

输出默认位于配置文件旁的 `var/`（仓库内直接运行时也为 `var/`）：

| 文件 | 用途 |
|---|---|
| `reports/YYYY-MM-DD.md` | 待审核日报 |
| `reports/YYYY-MM-DD.json` | 三级结构、证据定位和候选建议 |
| `reports/manual/YYYY-MM-DD.md` | 人工补充，重跑保留 |
| `review.sqlite3` | 本地脱敏事件、游标和同步状态 |
| `runs/last-run.json` | 来源状态、计数和产物路径 |
| `notion-plans/*.dry-run.json` | 离线同步预览，无远程写入 |

`var/` 包含私密数据，**不能跟源码一起上传或分享**。自动脱敏不能保证任意自然语言均不含隐私。

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

每日运行建议使用你自己的调度工具调用 `daily --through today --days 3`。它会回补最近三天；来源不可用时保留已有事件并返回不完整状态，不能将缺失记录解释为“没有工作”。本仓库不安装后台服务，不提供准点唤醒保证。休眠、网络及调度权限需要由运行环境处理。

## 可选功能

**SSH**：在本地私有配置的 `remote_sources` 中添加 SSH 别名。预先自行确认主机密钥和非交互认证，绝不关闭主机密钥校验。macOS 可复用 Keychain。配置和权限见 [配置说明](docs/configuration.md)。

**模型总结**：`model-preview` 先导出将要发送的有限脱敏片段；审阅后可用 `review --backend codex`。这会把片段发送给你所配置的模型提供方，`daily` 默认不会调用模型。模型工具活动、无效证据引用和证据升级会被拒绝。

**Notion**：复用你已有的工作记录和项目库，配置自己的 data source ID。令牌只从 `NOTION_TOKEN` 或 macOS Keychain 服务 `DailyReviewAgent.Notion` 读取。先生成 `notion-plan --online`，检查内容后用 `notion-sync --plan ... --approve ...` 执行。只更新 Agent 管理块；人工改动、已确认记录、重复日期/类型行等会阻止写入。不会自动修改 schema 或创建任务/知识；缺失项目补建需要显式配置和同次审批。

## 分享前检查

源码和合成示例可以公开，真实日报、原始会话、SQLite、个人映射、SSH 配置、Notion ID 与 Token 均应保留本地。本项目采用发布文件白名单与暂存区隐私检查：

```sh
python3 tools/check_release.py
```

检查工具不会打印发现的秘密值。它是辅助检查，不能替代对暂存文件的人工审阅。安全边界和威胁模型见 [PRIVACY.md](PRIVACY.md)，架构与测试范围见 [架构说明](docs/architecture.md)。

本项目基于观察到的 Codex JSONL 结构；该结构不是稳定公开协议。升级 Codex 后应先用 `doctor` 和小范围样本验证。日志缺失、截断、工具未记录结果或摘要选取上限都会限制覆盖范围。规则摘要是证据摘录，不替代个人反思。

## License

MIT，见 [LICENSE](LICENSE)。示例为合成内容，不包含真实用户会话。贡献与反馈见 [CONTRIBUTING.md](CONTRIBUTING.md)，漏洞处理见 [SECURITY.md](SECURITY.md)，验收范围见 [发布就绪说明](docs/release-readiness.md)。
