# 文档任务重启自动恢复

日期：2026-09-24。业务任务此前仅在 `GET /tasks/{id}` 时调用 `refresh`；上传登记后、提交中或取消结果未知时如果服务重启且无人打开该任务，状态可能长期停住。持久任务状态本身不等于自动恢复。

业务 API 生命周期现在启动独立 `DocumentTaskWorker`，用独立 Doclib 客户端轮询业务库的有界 keyset 任务列表，不向浏览器/Skill 暴露 Doclib。它处理 `uploaded`、`submitting`、`submitted`、`cancel_requested` 和提交响应未知的 `failed/doclib_submission_failed`；重交复用已有提交世代与 force 标记，Doclib 保证同世代只产生一次首次响应。已确认解析失败不被后台自动强制重试；缺失原文件改为明确的 `source_unavailable`，避免无限重试。取消及提交/完成竞态继续由业务状态 CAS 和 Doclib 释放墓碑裁决。系统没有用户、角色或权限管理。

Mac 定向测试覆盖：全新工作流实例恢复上传待提交任务并完成修订、提交中断、未知提交响应重放、未知取消响应重放、原件缺失终止、并发提交不会被迟到的原件缺失结果覆盖、扫描边界和 API 生命周期启停/启动失败清理。真实 Doclib Unix-socket 联调覆盖“业务库已登记任务→新工作流/扫描器提交→Doclib 完成→生成修订”，不是仅以 Mock 代替服务。最终完整业务套件 274 passed / 1 skipped；生命周期与 worker 定向测试 15 passed，真实服务定向测试 1 passed。Ruff 检查、两个新增文件的格式检查、JSON 和 diff 检查通过。

这证明本地 Mac 的单节点恢复机制，不证明隔离麒麟/NVIDIA 的容器重启、断网、模型失败、磁盘故障、容量或回退。BE-027 及最终生产验收仍以目标机演练为准。
