# Markdown 与 Obsidian

Obsidian 是可选的阅读和编辑入口，不需要插件、账户、云同步或 API。Agent 直接写普通 Markdown，也可使用其他编辑器。

## 新用户

在安装完成后的专用工作目录中运行：

```sh
python -m daily_review init --reports-dir "~/Documents/DailyReview"
python -m daily_review doctor
python -m daily_review daily --through today --days 3
```

然后在 Obsidian 仓库管理器中选择 **Open folder as vault / 打开文件夹作为仓库**，打开自己配置的文件夹。以后程序直接更新文件，无需每天手动导入。

已有仓库时，点击左下角当前仓库名称旁的切换入口，选择 **Manage vaults / 管理仓库**；也可在命令面板搜索 **Open another vault**。界面文字可能随版本和语言变化，参见 [官方仓库说明](https://help.obsidian.md/vault) 和 [管理仓库](https://obsidian.md/help/Files%2Band%2Bfolders/Manage%2Bvaults)。

## 已有日报：先预览，再迁移

```sh
# 默认只检查并展示数量、路径；不创建目标目录或改配置
python -m daily_review migrate-reports --to "~/Documents/DailyReview"
# 核对预览后执行复制与切换
python -m daily_review migrate-reports --to "~/Documents/DailyReview" --apply
```

需要一个已有的私有配置。相对目标路径按配置文件所在目录解释。迁移仅包含 `YYYY-MM-DD.md` 与 `manual/YYYY-MM-DD.md`，不会把 JSON、SQLite、会话日志、额外文档或配置复制进笔记库。

执行时保存配置备份及逐文件哈希清单到 `state_dir/migrations/`，复制并校验后才切换 `reports_dir`。原目录中的 Markdown 保留为迁移时的快照；后续只有新目录是活动输出，旧快照不会再自动更新。不要在新旧两处同时修改笔记。

同名且内容不同、来源/目标目录重叠、符号链接或正在运行的 Agent 会停止迁移，不提供强制覆盖。复制中断且配置尚未切换时，原目录仍有效；重试相同命令会复用完全一致的已复制文件。若提示冲突，先人工核对两份内容并保存到不同名称，不要直接删除个人笔记。未完整切换时仍请以配置和 `doctor` 输出为准。

## 人工编辑与冲突

- 在日报末尾“人工补充与最终反思”下编辑，或编辑 `manual/同日期.md`；后续生成会保留并带入。
- 自动管理区可能重新生成。在该区发现事实错误时，把修正与依据记入人工区。
- 不要删除 Agent 标记或人工反思标题；损坏标记、已有个人日记等同名文件会阻止覆盖。
- 若 Obsidian 的日记插件先创建了同名笔记，先保留它并更名，或给 Agent 配置一个专用子目录。Agent 不接管已有日记。
- 无需让 Obsidian 保持打开；它也不负责触发 Agent。

## 私有文件与回退

外部笔记目录同样含有隐私，默认 `var/` 忽略规则不能保护位于其他位置的笔记。选择专用私人文件夹；若所在目录受 Git 或云同步管理，先审查自己的忽略和同步设置。迁移命令不会修改已有 Obsidian 仓库的 Git、云同步或全局配置。

需要回退时先停止当前调度任务，保留新目录里的最新修改，再预览迁回原目录。若原目录快照已过时，迁移会拒绝冲突；应先人工合并并备份，再重新预览。不要直接恢复旧配置备份并覆盖后来的个人反思。
