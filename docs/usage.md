# 简明操作手册

## 第一次设置

完成 README 的安装步骤。希望用 Obsidian 阅读时，初始化自己的笔记目录：

```sh
python -m daily_review init --reports-dir "~/Documents/DailyReview"
python -m daily_review doctor
python -m daily_review daily --through today --days 3
```

已有 `config.local.json` 时不要重新初始化，按 [Obsidian 指南](obsidian.md) 预览并迁移笔记。`init` 不覆盖已有配置。

## 平时只需要做什么

1. 正常用 Codex 工作。旧项目、旧聊天继续使用，不用每天新建窗口，也不用先请 Codex 单独总结每个会话。
2. 运行 `daily`，或先按 [定时运行指南](scheduling.md) 配置自己的调度任务。下载和安装这个项目本身不会创建晚间自动任务。
3. 在 Obsidian 或其他 Markdown 编辑器中打开日期笔记，查看自动草稿。
4. 把线下工作、个人判断、事实修正和最终反思写到末尾“人工补充与最终反思”下方。这里会保留；自动摘要区重跑会更新。

本仓库只读取 Codex 记录，不采集其他助手的日志。Obsidian 不必在生成时保持打开。本地生成不需要 Notion 账户；配置了 SSH 来源时，远程采集仍需要网络。模型总结需要单独明确启用，默认规则草稿不调用模型。

## 日期与晚间截点

每条事件转换为配置中的时区后归入对应日期。旧会话今天新增的内容进入今天；午夜后继续工作进入下一天。工作事件数不是对话数或完成任务数。

例如你自行把任务设为当地时间 23:30，它生成的是截至执行时的阶段草稿。23:30 后的工作会在下次采集时补回原日期；默认每次重查最近三天并去重。不要把阶段草稿理解为当天已经结束。

## 提前生成、漏跑或补某一天

```sh
# 现在更新最近三天
python -m daily_review daily --through today --days 3
# 补采并更新某个具体日期（示例日期）
python -m daily_review daily --through 2026-01-15 --days 1
# 不采集，只用本地已有数据重新生成
python -m daily_review review --date 2026-01-15
```

这些命令在配置所在的专用工作目录中运行；在其他位置运行请加 `--config PATH`。退出码 2 或日报里的来源异常表示覆盖可能不完整，不能据此断言当天没有工作。

## 什么情况下才需要 Notion

日常阅读和反思可以全在本地完成。想发布某天草稿时，再按 README 进行在线预览、审阅和单次批准。不要让调度器自动批准刚生成的同步计划。

## 日报以工作记录为主体

先看工作概览和分项目工作记录，找当天处理内容、阶段结果与产物，再看简短复盘和后续事项。历史日报中的建议只代表当时视角，继续工作前应核对后续处理。人工补充仍写在末尾。完整格式及周月汇总可复用字段见 [格式说明](journal-format.md)。


如需每天实际由模型撰写“记录＋复盘”，请先检查 `model-preview` 的摘录范围并授权，再在调度器中使用 `daily --through today --days 3 --backend codex --model-timeout 360`。可对自己的任务持续授权，不必每日确认。原命令省略后端仍为纯规则模式；具体缓存、失败处理和权限边界见[调度指南](scheduling.md)。
