# 故障排查

## CodeArts 连接器没有出现

先运行配置检查，并在使用自定义目录时带上原安装参数。

```powershell
.\.venv\Scripts\python.exe scripts/configure.py --doctor --profiles desktop
```

打开桌面连接器的“自定义配置”，确认实际使用的文件位置与 `--codearts-home` 对应。Windows 验证使用 `~/.codearts/mcp/mcp_settings.json`，根对象为 `mcp`。`generated/codearts.connector.json` 用于桌面界面的自定义导入。

重新打开 CodeArts，再查看“我安装的连接器”。不同版本、不同安装方式的客户端用户目录可能不同，应以当前桌面实际读取的位置为准。

## 卡片显示连接失败

查看配置中的 Python 可执行文件、`codearts_queue.py` 和 `--database` 路径是否真实存在。目录移动、虚拟环境删除、Python 升级均可能使原绝对路径失效。

```powershell
.\.venv\Scripts\python.exe -c "import mcp; print('MCP_IMPORT_OK')"
.\.venv\Scripts\python.exe codearts_queue.py --help
```

确认后重新执行 `configure.py --apply`，再在客户端重启该连接器。直接运行 stdio 服务时，它会等待客户端输入协议消息，这是正常的服务运行状态。

## DSH 找不到四个队列工具

确认当前使用 desktop 还是 web，并检查对应 profile 的 patch 中有 `codearts-desktop-queue` 和 `codearts-review-notifications`。重新打开实际 profile，确认环境支持 DSH 的 MCP client 插件。

工具名固定使用 `mcp__codearts_queue__submit_task` 等前缀。`serverName` 应为 `codearts_queue`，这样交付通知才能观察正确的提交工具。

## 领取提示队列为空

在 DSH 查询刚才提交的任务 ID，核对是否提交成功，以及两端配置中的数据库是否指向同一个绝对路径。

使用配置脚本生成的配置时，派发端和执行端都会带 `--database`。两个客户端使用不同路径会建立各自独立的队列，应将它们统一到需要使用的本机数据库。

## 领取提示 busy 或过期

队列中的一个执行任务仍保留归属。先查看返回的活动任务 ID，在 CodeArts 桌面找到原任务并确认执行状态。

同一次领取的网络或工具重试继续使用原 `claim_key`。能恢复原上下文时，继续使用原令牌回报进度。需要重新派发时，由用户在客户端确认停止，DSH 完成取消记录后使用新请求键提交。

## 任务目录权限不足

在 CodeArts 桌面打开与任务 `project` 对应的项目，核对客户端的文件访问与终端权限。按照原生提示处理授权。受阻时由执行器通过 `report_delivery` 设置 `blocked: true` 并写明需要处理的事项，用户处理后在原任务上下文继续。

## 交付路径被拒绝

`artifacts` 中的路径需要真实存在并位于任务项目目录内。相对路径从任务项目根目录解析。符号链接和目录跳转会按实际解析后的路径核对。

将交付文件保存到正确项目后，再回报实际路径。原任务允许的范围需要调整时，先由用户明确新的要求。

## CodeArts 已交付，DSH 还没有检查

先用 DSH 的 `queue_status` 查看任务是否为 `awaiting_review`，并检查发起任务的 profile 是否正在运行。desktop 与 web 的通知归属分别保存。

如果 DSH 刚刚重启，给会话恢复和通知重试留出时间。若宿主升级后通知服务接口发生变化，可以在原 DSH 会话手动请求“查询任务 ID，检查实际交付并验收”，同时记录版本和恢复错误用于排查。

插件日志中的恢复错误以及 `review.sqlite` 的 `owners.error` 字段可供本机诊断。数据库和原始堆栈可能含项目路径，公开反馈前应脱敏。

## 安装提示并发修改或恢复失败

关闭正在写这些配置的客户端或编辑器，再根据安装输出的备份路径核对 `manifest.json`。安装器遇到并发变化时会停止当前替换，已应用文件在条件满足时回滚。

如果其他程序又修改了已写入文件，需要对照备份与当前文件合并。完成核对后重新执行安装与检查命令。原配置内容可能包括其他服务设置，备份应保存在本机受控目录。

## 更新或卸载后仍有旧工具

重新打开 DSH 与 CodeArts，或在 CodeArts 卡片中重启连接器，让进程加载新配置。旧进程可能继续持有它启动时的设置。

## 反馈问题时提供什么

- 操作系统与 Python 版本。
- DSH、CodeArts 桌面及本项目版本。
- 使用 desktop 还是 web，是否自定义用户目录。
- 从提交、领取到交付的具体复现步骤。
- 已脱敏的错误文字、任务状态及预期结果。

运行诊断、单元测试、真实桌面执行分别覆盖不同范围。排查结论应注明使用了哪一种验证方式。[验证范围](verification.md)
