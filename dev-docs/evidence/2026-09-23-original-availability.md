# 原件缺失与服务不可用反馈（FE-048）

系统仍是开放业务 API：没有登录、用户、角色、令牌或权限判断。任何能到达业务服务的客户端都可请求原文，因此部署必须保持既定的物理隔离和内网可达边界。

## 契约与行为

- `HEAD /api/business/documents/{id}/source` 与 GET 使用同一文件路径、大小和 SHA-256 核验；成功时只返回响应头，不传输正文。未知业务文档为 404，文件缺失／内容与记录不一致为 409，上传目录未配置为 503。
- Web 选择文档后先进行 HEAD 核验；核验中或失败时不创建 PDF／图片预览，也不显示原文打开／下载链接。409、404、503／连接失败有不同提示并可重试。切换文档后，旧核验结果不会覆盖新文档。
- 图片 GET 或解码失败触发预览错误状态，撤下预览和链接。PDF 正常路径仍按页跳转；只有核验成功后才创建 iframe。
- GET 在 HEAD 之后仍独立再次校验，因此两次请求之间文件变化不会让服务端返回未经校验的原件。内嵌 PDF 查看器若在 HEAD 后的 GET 阶段失败，浏览器不保证提供可靠错误事件；此处不宣称已识别所有竞态，后续真实浏览器／文件测试仍需覆盖。

## 验证

- `.venv/bin/python -m pytest tests/business -q --no-cov`：122 passed；目标测试检查健康 HEAD 无正文、404 和 409。
- `.venv/bin/ruff check mineru/business/api/app.py tests/business/test_business_api.py`：通过。现有两个 Python 文件不符合当前 Ruff formatter 的全文件风格；未因一行路由变更而机械重排历史代码。
- `pnpm --dir business-web test`：10/10；`pnpm --dir business-web build`：8 个离线资源。
- `python3 business-web/tests/source_availability_browser.py`：Playwright 验证 409／404／503 不展示预览或链接、重试恢复、图片解码失败再撤下预览。复核全流程、跨文档竞态、复核恢复、证据深链和断线恢复回归通过。

这些是 Mac 本地及模拟 API 验证，不等于四类真实样本或麒麟 x86_64＋NVIDIA 离线现场验收。
