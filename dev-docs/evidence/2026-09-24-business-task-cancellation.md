# 业务任务取消：跨库可恢复状态与真实反馈

日期：2026-09-24。基线：fork `master` 的 `60ecfced`。本轮新增业务库 schema 9、任务提交/取消状态机、开放业务 API、Web 和自有 Skill 的同一入口。没有账号、登录、角色或权限管理；Doclib 低层释放接口不暴露给 Web/Skill。

业务库先记录 `submitting` 才调用 Doclib；取消先记录 `cancel_requested` 才释放 `business:{task.id}` 的引用，最终保存 Doclib 首次逐批次结果和 `cancel_effect`。业务 SQLite 的终态裁决阻止迟到的提交、失败或完成修订覆盖取消。Doclib 响应未知时保持待确认，可从刷新或重复取消重放；已完成任务返回冲突，不伪装成取消。`not_submitted`、`queued_skipped`、`may_continue` 三类效果分别表达未提交、本次引用的队列批次确实跳过、底层计算可能继续；它们都不是对未来其它请求的永久禁算承诺。

验证包括上传前取消、共享批次、未知结果重放、提交中取消、完成竞争、业务 API/Skill 路由与 Web 确认弹窗；真实 Doclib 业务取消路由和 schema 9 备份恢复亦在本轮回归。最终完整 Mac 业务测试 258 passed / 1 skipped；官方单元测试 2828 passed / 4 skipped（在后续仅业务网关缓存选择修复之前运行）；Web Node 测试 12 passed、8 份离线资产构建、离线 Chromium 回归 23 passed。精确 Doclib 业务契约命令 23 passed；Ruff 与 diff 检查通过。

联调暴露了一个独立的完成缓存缺陷：同一 PDF 的 Doclib 历史批次可同时有 `1–13` 与较早 `1–10`，网关原先把所有 `done` 批次当作同一修订，因页面重叠将复用任务误判为 `parse_batch_invalid`。现从公开列表里选择不重叠且精确覆盖请求页集的批次组合；若没有可证明的组合则失败而非伪造修订。增加完整批次优先、互补分批以及无法无重叠覆盖三类回归；真实 Doclib 全文件复测 23 passed。失败诊断来自三次独立复现中的第 3 次，修复后完整业务回归亦通过。

边界：本地 Mac 的合成与真实服务测试不能证明隔离麒麟 x86_64＋NVIDIA 的 GPU 抢占、模型质量、离线发布或回退。业务库 schema 8 不隐式迁移，旧文件原样保留；历史文档中的 schema 8 记录是当时证据，不代表当前部署版本。并发 `force` 重试的“单次意图只创建一个新批次”仍需补充协议与测试，因此 FE-016 以及最终目标暂不标完成。
