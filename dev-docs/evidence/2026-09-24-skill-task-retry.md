# 自有 Skill 的业务任务重试

日期：2026-09-24。范围：本 fork 的 `business-documents` Skill，经统一开放业务 API 重试一个已有任务；不接官方 Skill、Doclib 内部接口或模型服务。

业务 Web/API 已有 `POST /api/business/tasks/{id}/retry`，自有 Skill 此前仅能查询与取消任务。现在提供 `retry TASK_ID --confirm-write`；说明要求用户明确请求该任务重试，确认业务 API 指向批准的隔离服务。确认标志仅是 Agent 侧写操作提醒，不是账号或权限机制。

一次命令只发送一次 POST。若连接失败、HTTP 408/5xx、成功响应损坏或任务身份不符，客户端仅 GET 同一任务，返回 `retry_outcome_unconfirmed` 和当时观察到的状态；GET 失败则报告未知，不推断 POST 未发生。HTTP 409 等明确业务冲突直接返回错误，不追加 GET 或 POST。任务 GET 的当前状态不能证明这次重试是否被接受；Agent 需要继续查询或请用户决定下一步。

定向 `tests/business/test_skill_contract.py` 为 14 passed，验证写入确认、同源业务 API 路径、未知结果只读核对、损坏成功响应和 409 处理。完整 `tests/business` 为 350 passed、2 skipped、2 个第三方依赖警告；Ruff lint、特性清单 JSON 与差异检查通过。Skill 元信息/目录通过 `skill-creator` 的 `quick_validate.py`。这些是 Mac 模拟连接与既有真实 FastAPI 测试替身范围；麒麟真实网络故障与 GPU/模型任务未验收。
