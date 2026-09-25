# Doclib 连接后读取超时的业务任务恢复

日期：2026-09-25。范围：开放业务 API 到内部 Doclib 的任务提交、解析轮询、PDF 原件页数核对与取消；不改变模型侧和权限架构。

发现：DoclibClient 在 HTTP 连接成功后仍可能抛出 `httpx.ReadTimeout`。业务工作流原先只归一化 MinerU 异常、`OSError` 等，读取超时会从提交或 `GET /tasks/{id}` 的刷新路径逃逸。持久任务状态通常仍在，但 API 不能稳定返回可恢复的业务状态，后台恢复线程会记录异常并等待下一轮。

修正：业务工作流把 `httpx.RequestError` 视为传输结果未知。提交保留并重用已持久化的任务提交世代和 `force` 意图，不再生成新轮次；解析批次或 PDF 元数据读取超时保持 `submitted`，不误记解析失败或创建修订；取消释放响应超时保持 `cancel_requested`，不宣称已停止。后续同一意图可由后台扫描、任务查询或明确重试继续推进。仅处理传输异常，不吞掉业务数据一致性错误。

新回归先在旧代码上复现了提交和轮询的 `ReadTimeout` 逃逸；修正后，重新创建工作流对象仍能用同一提交世代完成，PDF 元数据超时后能继续完成修订，取消超时后能重放释放。定向文档工作流与后台扫描测试 36 passed；完整 Mac 业务套件 361 passed、2 skipped、2 个第三方依赖警告；Ruff lint 通过。

这些是 Mock 传输异常与本地 SQLite 的状态机证据，不是麒麟隔离机器上的真实断线、容器重启、GPU 模型加载或四类样本验收。目标机仍须在 `uploaded/submitting/submitted/cancel_requested` 各状态做受控重启与网络故障演练。
