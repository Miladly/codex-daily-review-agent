# 首次使用与故障处理

## 先确认自己运行的是哪种模式

默认是本地规则整理，不会调用模型。需要由模型撰写记录与复盘时，先用规则模式采集、检查 `model-preview` 的摘录，再明确使用 `--backend codex`。可以对自己的定时任务持续授权，无需每天确认；这不包含 Notion 发布或公开上传日报。

```sh
python -m daily_review doctor
python -m daily_review doctor --backend codex
python -m daily_review status --through yesterday --backend codex
```

模型适配器目前实机验证过 `codex-cli 0.163.0-alpha.11.1`。这不是所有 CLI 版本均兼容的承诺；doctor 会检查 `--ephemeral`、`--output-schema`、`--ignore-user-config` 和 `--skip-git-repo-check`。若缺少这些安全/结构化接口，模型模式会停止，规则模式仍可使用。不要为了兼容而删除安全隔离参数。

第一条检查基本来源，第二条另外要求可用的 CLI 接口。doctor 不调用模型，不验证账户登录、余额或网络通路；接口可用不等于模型请求已成功。先手动成功生成一份，再配置定时任务。

## 常见症状

| 症状 | 检查与处理 |
|---|---|
| 克隆提示找不到仓库或无权限 | 仓库公开前需访问权限；不要把 Token 拼进下载链接。 |
| Python 安装失败 | 需要 Python 3.10+；macOS/Linux 使用虚拟环境，Windows 使用 WSL2。构建源码时需要 setuptools 77+，安装已构建 wheel 无需联网构建。 |
| 找不到 Codex 日志 | `codex_home` 指向包含 sessions 或 archived_sessions 的目录；可能需要先产生一段 Codex 会话。不要指向单个 JSONL。 |
| doctor 提示没有 Codex CLI | 规则模式可继续；模型模式需安装 CLI 或在私有配置中设置 codex_binary。模型不使用单独填写在此仓库的 API Key。 |
| 终端能运行，定时任务找不到 CLI | 调度器的 PATH 往往不同。配置 codex_binary 的绝对路径；程序会尝试识别已观察到的 macOS 桌面捆绑路径，但不保证覆盖所有版本和安装位置。 |
| CLI 位于配置旁的子目录 | codex_binary 支持相对路径，以配置所在目录解释；自定义模型名 model 必须是非空字符串。 |
| 运行数分钟没有最终结果 | daily 会在 stderr 输出 collection/model/day 阶段、日期和超时限额；stdout 最后仍是一份 JSON。模型默认每日期最多等待240秒，可显式调整。首次 SSH 补采也可能较长。不要重复启动；同一状态目录有进程锁。 |
| 想只保留最终 JSON | 给 daily 加 `--quiet` 关闭进度。它不会关闭失败退出码或最终状态。 |
| 日报存在，但担心昨晚漏跑 | 使用 status 检查具体日期；不能仅凭文件存在认定最新运行完成。 |
| 模型失败后笔记仍存在 | 它是保留下来的规则记录，正文会标注模型提炼未完成。检查返回的 fallback、model_errors 和本地私有 model-failures；修复登录、网络或额度后用完整模型命令重跑。 |
| SSH 来源不可用 | 核对准确别名、端口、网络、主机密钥和非交互认证；不关闭主机校验。保留历史事件，缺失不等于没工作。 |
| Agent 把自己也记进来了 | init 在 Codex 内执行时会保存当前会话排除项；旧配置请自行补齐 Agent 的所有工作目录和会话 ID。换聊天启动 Agent 时也应检查排除规则。 |
| 同名笔记拒绝覆盖 | 先保留个人笔记，使用专用子目录；不要删除保护标记来强制覆盖。 |
| 数据库损坏或提示新版本 schema | 停止该状态目录的任务，私下备份整个 state_dir，包含现存 WAL/SHM；使用兼容版本再诊断。不要删除数据库、手改版本号或上传数据库到 Issue。程序不会自动重置或降级。 |

## status 怎样判断

status 默认检查配置时区的“昨天”，可用 `--through today` 或指定日期。仅读取 `runs/last-run.json` 及文件存在状态，不读取会话、SQLite事件或笔记正文，也不调用模型、不写文件。

- `no_completed_run`：没有完成记录；先运行 daily。
- `expected_date_not_processed`：最近完成的一轮没有覆盖预期日期；用同一后端补跑。
- `run_unfinished`：当前或上次运行尚未写入最终结果；正在运行时先等待，进程已结束时再重跑。程序以本地完成标记检测，不把旧成功记录误当新一轮完成。
- `last_run_incomplete`：最近运行有模型或来源错误，结合最终元数据判断。
- `model_review_incomplete`：要求模型模式，但该日期只有规则记录或模型失败。
- `note_missing`：完成记录对应的笔记已不在当前输出目录。
- `timezone_changed`：完成记录与当前时区不同，先确认配置再重跑。

健康时退出0；需要处理时退出2；配置或数据库问题退出1。健康只说明检查到的记录符合要求，status 不检查操作系统调度器是否启用，也不能保证未来唤醒。

## 私密地求助

可提供操作系统、Python和项目版本、失败阶段/错误类别，以及合成复现。不要直接粘贴完整 doctor 输出、JSON配置、真实会话、SQLite、日报、SSH文件或含个人信息的截图。需要附命令时，用示例路径替换本机路径，移除项目名、主机、ID及凭据。参见 [安全说明](../SECURITY.md)。
