# 自有 Skill 上传请求核对入口

自有 `business-documents` Skill 现通过与 Web 相同的开放业务 API 暴露只读 `upload-request REQUEST_KEY` 命令；它不读取或重传本地文件，不要求 `--confirm-write`，也不调用官方 Skill、Doclib 或模型服务。查询到已持久化的请求时返回 `state=accepted`、原业务文档与**当前**任务；HTTP 404 变为 `state=not_recorded_at_lookup`，只表明查询瞬间未查到，不证明早先上传被拒绝，Agent 不能据此自动生成新键重传。无效键在发请求前拒绝，5xx/连接错误继续显式报错。

`upload` 的 HTTP 408/5xx、连接失败及成功响应不可读均按结果未知处理，并保留原 `request_key` 供先查询、后经用户明确指示同键重试；这与 Web 的不确定写入语义一致。`--confirm-write` 仍只在用户明确要求该文件写入并核对隔离业务服务地址后使用，不是身份鉴权。开放系统不增加用户、角色或权限管理。

验证：Skill 定向契约 11 通过，覆盖接受/404/503/无效键/不完整响应、上传 503 结果未知，以及对真实 FastAPI 应用的上传→按键查询→任务状态变化查询；完整 Mac 业务测试 226 通过、1 跳过，Ruff 通过，自有 Skill frontmatter/结构校验通过。未在隔离麒麟 x86_64 + NVIDIA 上运行，未模拟生产网络真实故障，也未验证四类真实文档质量。
