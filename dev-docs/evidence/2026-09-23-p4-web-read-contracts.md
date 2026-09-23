# P4 业务前端读取契约：文档列表、能力与原文

日期：2026-09-23。源码基线：`d862644d`，本次修改在其后。环境：Mac Apple Silicon，Python 3.13.5；不是麒麟目标机。

## 已实现

- `GET /api/business/documents?limit=&offset=&template_code=&status=`：持久 SQLite 分页，返回最新入库任务；不按用户或所有者过滤，最大页长 100。列表仅暴露业务 ID、文件名、哈希、大小、模板与任务状态，不暴露存储键、Doclib parse ID 或内部路径。
- `GET /api/business/capabilities`：从实际上传仓库大小限制及 MinerU 文件类型常量导出扩展名/档位，供 Web 表单在提交前约束输入。
- `GET /api/business/documents/{id}/source`：以业务 ID 查找原文，核验记录的大小与 SHA-256 后返回。PDF/图片内联，HTML/Office 等附件下载；`nosniff`、`no-store`、sandbox 响应头；缺失、篡改、未知 ID 分别返回 409/404。无登录/权限检查。

## 验证

- `.venv/bin/ruff check mineru/business tests/business`：通过。
- `.venv/bin/python -m pytest -q tests/business/test_business_api.py tests/business/test_business_store.py`：11 passed，2 个依赖弃用警告。
- `.venv/bin/python -m pytest -q tests/business`：在允许本地 Unix/TCP socket 绑定的环境中 **85 passed，2 warnings**。默认受限沙箱首轮出现 1 failed、16 errors，原因均为 Doclib 测试进程 `socket.bind` 被拒；已在有权限条件下完整重跑通过。

## 限制与后续

- 尚未构建业务 Web，也未进行浏览器窄屏/键盘/原文预览验收；此契约测试不等于 UI 可用。
- 当前哈希在响应前验证，单机上传目录仍须按设计限制为业务 API 写、worker 只读；同 UID/管理员并发篡改不是该检查可防的安全边界。
- Office/HTML 原文不宣称浏览器可精确定位；真实样本定位能力仍待验证。
