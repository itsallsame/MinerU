# P4 业务 Web 第一组可运行界面

日期：2026-09-23。源码基线：`f6a6aa6a`，本次修改在其后。环境：Mac Apple Silicon，Node 24.2.0、pnpm、Python 3.13/3.14、Playwright Chromium 1.58；目标麒麟/NVIDIA 未运行。

## 范围

- `business-web/` 是零第三方依赖的静态模块；`pnpm test` 调用 Node 内置测试，`pnpm build` 只复制确定的五个源文件、校验无远程 URL 并生成逐文件 SHA-256 清单。`dist/` 由业务代码镜像复制，通过显式 `MINERU_BUSINESS_WEB_ROOT` 同源挂载于业务 API；生产缺少页面时拒绝启动，不依赖 site-packages 位置推算。
- UI 实际调用开放业务 API：服务能力与模板、分页文档列表、状态/模板筛选、多文件逐个上传、文件类型与大小预检、PDF/图片档位选择、Office 等 Flash 规则、状态轮询、失败重试、原文入口和修订列表。界面不包含登录、用户或权限管理。
- PDF/图片尝试浏览器内联预览；HTML/Office 等给出下载与“不能按 PDF 精确定位”的说明。静态页面使用 `textContent` 创建动态文档名/模板名/错误文本，未把服务端文本插入 HTML 解析器。
- 业务镜像构建要求 `WEB_ASSET_MANIFEST_SHA256`，Dockerfile 校验打入的 manifest；发布清单再次核对 `dist/` 全部文件与业务镜像的哈希标签，避免未入 Git 的静态产物冒充同一源码发布。

## 验证

- `cd business-web && pnpm test && pnpm build`：3 个纯函数测试通过、5 个离线静态资源构建成功；无依赖下载。
- `.venv/bin/python -m pytest -q tests/business/test_business_api.py tests/business/test_business_server.py`：8 passed，2 个依赖弃用警告。同源根路径静态页面、JS 和业务 API 路由共存测试通过。
- `.venv/bin/python -m pytest -q tests/business/test_release_manifest.py`：5 passed；含前端文件篡改、镜像缺少对应哈希标签的拒绝用例。
- `.venv/bin/python -m pytest -q tests/business`：在允许本地 Unix/TCP socket 的 Mac 环境最终 **88 passed，2 个依赖弃用警告**，包含显式生产 Web 路径测试。Compose 占位变量 `docker compose ... config --quiet` 通过。
- 临时本机业务 API 地址 `127.0.0.1:18088`（不接入 Doclib）由 Playwright Chromium 打开，实际能力/模板/空文档 API 为真实响应；上传和后续列表响应由浏览器测试脚本拦截模拟，验证不支持格式无请求、PDF 提交含 Standard 档位、原生 HTML 不提交显式档位、混合批次逐文件反馈、状态与模板筛选、文档详情和窄屏无横向溢出。命令：`/opt/homebrew/opt/python@3.14/bin/python3.14 business-web/tests/browser_smoke.py http://127.0.0.1:18088`，通过。
- 空库桌面与窄屏截图：[桌面](screenshots/p4-web-desktop.png)、[窄屏](screenshots/p4-web-mobile.png)。截图仅显示本机空库，不包含用户业务资料。

## 未完成与风险

- P4 复核/成果/模板管理/搜索/结构树等仍未完成；浏览器脚本使用拦截上传，并不证明 Doclib 或模型解析端到端成功。
- 任务完成依赖客户端轮询 `GET /tasks/{id}`，当前只轮询可见页；不可见旧任务的自动收敛需要后端后台调和机制或后续 UI 策略。
- PDF/图片浏览器预览尚未用真实原文逐格式验收；Office 坐标/预览不能推断可用。生产入口请求体限额、CSP、真实麒麟离线构建/运行仍开放。
