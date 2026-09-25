# 任务取消请求键与 Web/Skill 故障恢复

日期：2026-09-25。范围：本 fork 的开放业务 API、SQLite 任务取消意图、业务 Web、自有 `business-documents` Skill 和离线状态备份。

此前 `POST /api/business/tasks/{id}/cancel` 的响应丢失后，客户端只能读取任务当前状态，无法证明**这一次**取消请求是否受理。当前业务 SQLite schema 17 新增 `task_cancel_requests`：可选的 `Idempotency-Key`（16–128 位 ASCII 字母、数字、下划线或短横线）与任务 ID、取消意图在同一事务中提交。同键同任务返回当前任务，不重新建立意图；同键异任务返回 409。`GET /api/business/task-cancel-requests/{key}` 只读返回绑定任务，404 仅表示查询当时没有记录，不证明先前未知的 POST 被拒。已完成任务不能取消，且该判断移入事务内，避免检查与写入之间的竞争。原有未带键 API 保持可调用，但本 fork 的 Web/Skill 均带键。

Web 在发 POST 前持久化任务 ID、随机键和待核对状态；无法保存时拒绝发送。未知响应只按原键 GET，刷新后仍可核对；404 不自动重发，只允许用户明确确认后**同键**重试。Skill 的 `cancel` 也只发一次 POST，未知结果只读按键核对，返回键供 `task-cancel-request` 或明确确认的同键重试。两者不直接调用 Doclib；键不是身份/认证，也不能证明共享或运行中的 GPU 计算已停止。后台 `cancel_requested` 恢复与 Doclib 消费者释放语义不变。

Mac 验证：并发同键存储测试、API 同键回放/异任务冲突、Skill 503/不可读成功响应/404/只读核对、备份缺表拒绝和恢复测试通过；完整 `tests/business` 为 393 通过、2 跳过、2 个依赖警告。Web 单元测试 13 通过、8 个离线资产构建成功、28 个 Chromium 浏览器回归通过，其中取消测试覆盖响应丢失、刷新恢复、暂时不可读、404、明确同键重试和浏览器存储不可写。Ruff 与 `git diff --check` 通过。

schema 16 不迁移或自动删除：如需保留旧库，先归档，部署时以新空库初始化 schema 17。目标隔离麒麟 x86_64＋NVIDIA、真实网络故障和四类已标注原文仍未验收；本证据不能替代上线验收。
