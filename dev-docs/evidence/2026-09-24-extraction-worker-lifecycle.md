# 提取 worker 启停与异常恢复回归（2026-09-24）

背景：QA-002 覆盖率基线显示 `extraction_worker.py` 的部分生命周期和异常分支尚未被测试。此轮只补业务后台 worker 的确定性测试，不改生产队列语义。

新增 `tests/business/test_extraction_worker.py`，用事件同步而非固定睡眠验证：非正轮询间隔拒绝；空队列时可停止并重新启动，且禁止重复启动；一次未预期异常会记录错误并继续轮询；处理中的 worker 超时停止时不会启动第二个 worker，解除阻塞后退出且不再多取任务。现有 `test_extraction_queue.py` 继续验证 SQLite 租约过期、旧令牌失效、部分候选清理和最大重试数。

验证：

- `.venv/bin/python -m pytest -q tests/business/test_extraction_worker.py`：4 passed。
- `.venv/bin/ruff check tests/business/test_extraction_worker.py`：通过。
- `.venv/bin/python -m pytest -q tests/business --tb=short`：175 passed、1 个 opt-in 浏览器测试跳过。第一次在受限环境中因本地 Unix/TCP socket 绑定被禁止而产生 19 errors/1 failure；允许本机测试 socket 后原命令通过，故这些不是代码回归。

边界：使用模拟提取器测试线程控制，并未证明进程被强杀后的端到端恢复、麒麟/NVIDIA 环境、真实四类文档的字段质量；这些仍需独立验收。本轮没有增加用户、登录或权限管理，系统保持开放入口。
