# 使用与工具参数

## 从派发到验收

1. 在 DSH 中明确项目绝对路径、工作范围、完成标准，并授权交给 CodeArts 桌面。
2. DSH 调用 `mcp__codearts_queue__submit_task`，返回 `ca-` 开头的任务 ID。
3. 在 CodeArts 桌面打开相应项目，选择 `dsh-task-queue` 连接器，发送“领取并处理一个 DSH 任务”。
4. CodeArts 领取后读取 `task.project`、`task.instructions`、`task.acceptance`，使用原生工具完成任务，按需回报进度。
5. 完成后，CodeArts 调用 `report_delivery`，列出实际交付文件与测试结果。
6. 原 DSH 会话收到通知，先查询当前状态，再独立读取文件并执行必要测试。
7. DSH 调用 `review_delivery`。通过时进入 `accepted`，退回时携带反馈重新排队，等待下次手动领取。

CodeArts 工作目录应与任务目录对应。跨目录访问产生的权限请求交由客户端正常处理。DSH 与 CodeArts 应使用清晰、互不冲突的工作分工，避免同时修改同一个文件。

## 任务状态

| 状态 | 含义 | 下一步 |
| --- | --- | --- |
| `queued` | 排队等待领取，包含被退回的修订任务 | 用户在桌面手动领取 |
| `running` | 已被一个领取操作持有 | 执行、回报进度或交付 |
| `needs_attention` | 执行器回报受阻，需要权限、输入或恢复 | 用户处理后，在原领取上下文继续 |
| `awaiting_review` | 执行器已交付 | DSH 独立检查并给出结论 |
| `accepted` | DSH 已记录通过证据 | 任务完成 |
| `canceled` | 已取消 | 如有新需求，使用新请求键提交 |

`cancel_requested` 是运行中任务的取消请求标志。它要求执行器停止并交代当前进度。任务是否真正停止，需要结合原生桌面执行情况确认。

## DSH 派发端工具

工具名在 DSH 中带有 `mcp__codearts_queue__` 前缀。

### `submit_task`

| 参数 | 类型 | 含义 |
| --- | --- | --- |
| `project_dir` | string | 已存在的项目绝对目录 |
| `request_key` | string | 当前项目内的稳定请求键，最长 120 字符 |
| `title` | string | 简短任务名称 |
| `instructions` | string | 具体范围与交付要求，最长 100000 字符 |
| `acceptance` | string[] | 1 至 30 项非空验收标准 |

同一项目和同一请求键用于重试去重。重试内容应保持一致。已完成任务的新一轮工作使用新键。

```json
{
  "project_dir": "C:/projects/demo",
  "request_key": "hello-file-001",
  "title": "创建示例文件",
  "instructions": "在项目根目录创建 hello.txt，内容为 HELLO_CODEARTS 加一个换行。",
  "acceptance": ["文件位于项目根目录", "文件内容准确"]
}
```

### `queue_status`

传入 `task_id` 查询指定任务。省略或使用空字符串时，返回状态数量与最近 30 个任务。返回内容包含指令、进度、交付与验收回报，适合主 DSH 核查。

### `review_delivery`

传入 `task_id`、`accepted` 和非空 `evidence`。只有 `awaiting_review` 可进入此操作。证据应写明 DSH 实际读取的文件、运行的命令、核对的结果以及与用户标准的对应关系。

当 `accepted` 为 `false` 时，任务回到 `queued`，修订领取会得到反馈。

### `cancel_task`

传入 `task_id`。排队任务可直接取消。运行中任务先设置取消请求，执行器在进度响应中查看 `cancel_requested`。

确认桌面执行已由用户或原生客户端停止后，DSH 才可以传入 `executor_stopped: true` 完成取消记录。取消验收等待中的任务也会使其进入终态。

## CodeArts 执行端工具

### `claim_next`

传入 `claim_key`，表示这一次手动领取操作。同一操作发生重试时继续使用原键，新一次领取使用新键。可选 `lease_seconds` 为 300 至 7200 秒，默认 3600 秒。

返回任务时，`task` 包含完整要求和本地归属令牌 `lease_token`。令牌用于后续进度与交付调用，应保留在该任务的执行上下文中。

全队列同一时刻持有一个执行中的任务。其他领取会得到 `busy` 和活动任务 ID。过期的领取会标记需要人工关注，原执行器状态需要核实后再处置。空队列返回等待说明，用户下一次发起领取时再处理。

### `report_progress`

传入 `task_id`、`lease_token`、`summary`，回报真实进展并续期一小时。响应中的 `cancel_requested` 用于检查取消要求。

### `report_delivery`

传入 `task_id`、`lease_token`、`summary`、`artifacts`、`tests`，可选 `blocked` 默认 `false`。

- `artifacts` 为真实存在的文件或目录，可用项目内相对路径，也可用项目内绝对路径。
- `tests` 为实际执行的测试及其结果说明。
- `blocked: true` 表示执行受阻，状态进入 `needs_attention`。
- 正常交付进入 `awaiting_review`，等待原 DSH 的独立核查。

建议在回报中区分完成内容、测试证据和仍待用户处理的事项。模型自然语言总结与交付文件应互相核对。

## 中断、重试与取消

提交响应丢失时，使用原 `request_key` 和原内容重试。领取响应丢失时，使用原 `claim_key` 重试。

进度或交付响应不明确时，先由 DSH 查询任务状态，核对数据库中实际记录。已经进入验收等待的交付由主会话继续检查。

执行器中断或领取过期后，先查看桌面中的原任务是否仍在运行。能恢复原上下文时，继续使用原归属回报进展。需要重新派发时，在原生客户端确认停止，完成取消记录，再使用新请求键提交明确的新任务。

## DSH 通知与检查

派发成功的工具事件建立任务与原会话的绑定。重复提交保留原绑定。通知监测以约两秒间隔查询本机数据，恢复与重试可产生额外等待。

原会话已加载时，通知通过下一轮消息排队进入。重启后，插件通过 DSH 当前会话组合服务恢复该会话的模型和预设。恢复失败会记录错误并稍后重试，人工处理可先通过 `queue_status` 查看交付。

该通知具有本地租约与交付版本去重。应用崩溃和文件持久化存在时间窗口，操作结果以队列当前状态和真实项目文件为准。重复通知到达时，DSH 应先查状态再决定是否需要继续检查。
