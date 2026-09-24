# 开放业务上传入口请求体限额（Mac 回归）

开放系统没有登录、用户或权限层，`POST /api/business/documents` 必须在 multipart 解析与临时文件 spooling 之前约束请求体。原业务存储层只有文件读取时的 `max_upload_bytes`，不足以约束更早的请求体接收阶段。

新增业务 API 的 ASGI 接收边界：带 `Content-Length` 的超限请求立即返回 413；无长度或虚报较短长度的请求在累计接收超过 `max_upload_bytes + 64 KiB` 时返回 413。额外 64 KiB 供 multipart 字段和边界使用；文件存储层仍按原配置执行精确文件大小上限。此边界只作用于文档提交路由，不改变读取、搜索或其他写入接口，也不增加认证系统或额外容器。

验证：

- `PYTHONPATH=. .venv/bin/pytest -q tests/business/test_upload_body_limit.py`：4 通过，覆盖普通超限、无长度流、虚报短长度和非法长度。
- `PYTHONPATH=. .venv/bin/pytest -q tests/business`：186 通过、1 个 opt-in 跳过、2 个依赖弃用警告；获准本地 Unix/TCP 绑定后完成。
- Ruff lint、仅新文件的 format check 和 `git diff --check` 通过。

限制：这里验证的是 Mac ASGI/TestClient 边界，不证明目标麒麟 Docker 端口上的真实连接行为或代理缓冲行为。目标机物理隔离、NVIDIA 模型解析和四类真实样本仍未验收。
