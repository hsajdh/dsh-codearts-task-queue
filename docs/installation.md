# 安装与本地接入

## 先确认使用环境

本项目把同一台电脑上的 DSH 与 CodeArts 桌面通过一个 SQLite 文件连接起来。双方应能访问同一个项目目录和同一个数据库绝对路径。数据目录适合放在本机磁盘，SQLite 并发语义依赖本机文件系统。

先完成 DSH、CodeArts 桌面的安装与正常登录，并分别运行一次。Windows 验证使用的 DSH profile 位于 `~/.dsh/profiles/desktop`，CodeArts 连接设置位于 `~/.codearts/mcp/mcp_settings.json`。客户端使用自定义用户目录时，需要把实际位置传给配置脚本。

## 安装源码与 Python 环境

选择一个长期保留的目录。示例使用 `C:/projects`，也可使用包含中文或空格的路径。

```powershell
Set-Location C:/projects
git clone https://github.com/hsajdh/dsh-codearts-task-queue.git
Set-Location dsh-codearts-task-queue
python --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

可以直接运行虚拟环境中的 Python，命令含空格时在 PowerShell 中使用调用运算符 `&`。

```powershell
& "C:/my projects/dsh-codearts-task-queue/.venv/Scripts/python.exe" --version
```

依赖安装需要访问 Python 软件源。完成安装后，本机队列与通知组件通过 stdio 和本机文件工作。模型服务的联网与计费由官方客户端处理。

## 自动接入桌面 profile

```powershell
.\.venv\Scripts\python.exe scripts/configure.py --apply --profiles desktop
.\.venv\Scripts\python.exe scripts/configure.py --doctor --profiles desktop
```

安装器更新下面三个位置。

| 文件 | 写入内容 |
| --- | --- |
| `~/.dsh/profiles/desktop/cordis.patch.yml` | 派发端 MCP 与交付通知插件 |
| `~/.dsh/AGENTS.md` | 派发、手动领取、独立验收与取消约定 |
| `~/.codearts/mcp/mcp_settings.json` | `dsh-task-queue` 执行端连接器 |

文件修改前会保存原文和恢复清单。默认备份在 `~/.dsh-codearts-task-queue/backups`，Windows 下为当前用户和 SYSTEM 设置访问权限。DSH patch 由 YAML 解析器写回，格式与注释原文可以在备份中查看。

重复运行同一接入命令会更新本项目负责的条目。安装器对旧的 `codearts-desktop-queue`、`codearts-review-notifications` 和 `dsh-task-queue` 条目进行替换，因此这些标识应保留给本项目。

重新打开两个客户端。在 CodeArts 中打开“扩展 → 连接器 → 我安装的连接器”，确认 `dsh-task-queue` 显示“已连接”，再使用连接器创建任务会话。`--doctor` 检查配置和已有队列的完整性，卡片状态用于确认实际桌面连接。

## 同时接入 desktop 与 web

先各自运行一次 DSH desktop 与 web，让它们建立 profile，然后运行下面命令。

```powershell
.\.venv\Scripts\python.exe scripts/configure.py --apply --profiles desktop web
.\.venv\Scripts\python.exe scripts/configure.py --doctor --profiles desktop web
```

两端共享一个任务库，任务通知保留发起 profile 的归属。desktop 提交的任务由 desktop 发送原会话通知，web 提交的任务由 web 处理。

## 自定义本地目录

```powershell
.\.venv\Scripts\python.exe scripts/configure.py --apply `
  --profiles desktop `
  --dsh-home "D:/agent-data/dsh" `
  --codearts-home "D:/agent-data/codearts" `
  --data-dir "D:/agent-data/codearts-task-queue"
```

`--dsh-home` 指向含有 `profiles` 的 DSH 用户根目录。`--codearts-home` 指向实际桌面用户配置根目录，其下使用 `mcp/mcp_settings.json`。`--data-dir` 保存通知记录与备份。`--database` 可单独指定共享队列的绝对路径。

后续检查、更新和卸载时，继续使用相同目录参数。DSH 本身的 `DSH_HOME` 应与接入时选定的用户根目录对应。

## 手动导入配置

在仓库根目录运行生成命令。

```powershell
.\.venv\Scripts\python.exe scripts/configure.py --profiles desktop
```

`generated` 目录中包含已填入本机绝对路径的三个文件。

1. `desktop.patch.yml`，将内容追加到 DSH desktop 的 `cordis.patch.yml`。先搜索已有同名 ID，确保最终每个入口只有一份。
2. `codearts.connector.json`，在 CodeArts 桌面的连接器“自定义配置”中合并这段连接器对象。使用文件编辑时，它位于根对象的 `mcp` 中。
3. `dsh-instructions.md`，将对应规则合并到 DSH 用户级 `AGENTS.md`。

重新打开客户端，核对连接器卡片与 DSH 的四个工具。手动方式适合希望审阅每一处配置的人。[CodeArts 官方配置说明](https://support.huaweicloud.com/usermanual-codeartsdesktop/desktop_0015.html)

静态样例位于 `examples`。其中的 `REPO_DIR`、`PYTHON_EXE`、`DATA_DIR` 均需替换为本机绝对路径。生成器输出可以直接作为本机配置来源。

## 迁移已有本地原型

如果已经使用早期定制队列，可将 `--database` 指向原 SQLite 文件，数据结构兼容本仓库的队列格式。先让当前运行任务完成或经客户端正常停止，再进行配置切换，并备份队列。

```powershell
.\.venv\Scripts\python.exe scripts/configure.py --apply --profiles desktop web `
  --database "D:/existing-agent-data/codearts-queue.sqlite"
```

交付通知归属单独保存于 `review.sqlite`。迁移前已提交任务的归属记录需要与原通知状态库对应。缺少归属的旧交付可由 DSH 手动调用 `queue_status` 检查并验收。新提交任务会建立新的原会话归属。

## 升级

在运行任务结束或正常停止后，备份运行数据，更新源码并重新安装固定依赖，再运行配置和检查命令。

```powershell
git pull --ff-only
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts/configure.py --apply --profiles desktop
.\.venv\Scripts\python.exe scripts/configure.py --doctor --profiles desktop
```

重新打开客户端，再核对版本记录与验证范围。DSH 的预览版本可能调整会话服务，通知恢复依赖当前宿主接口。

## 卸载与恢复

```powershell
.\.venv\Scripts\python.exe scripts/configure.py --uninstall --profiles desktop
```

卸载命令移除选定 profile 的本项目入口、CodeArts 连接器及带项目标记的 DSH 规则段。两端重新打开后加载结果。任务数据库、通知状态和配置备份保存在 `--data-dir` 指向的目录，可按自己的保留需求另行处理。

恢复某次安装前的原文时，先关闭正在修改这些配置的客户端，读取该次备份的 `manifest.json`。每条记录说明目标绝对路径、原文件是否存在及对应的 `.original` 文件。根据记录核对后复制回目标位置，并重新打开客户端。备份可能包含你的其他私有配置，应保存在本机受控目录。
