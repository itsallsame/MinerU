# FE-050 浏览器关键流程回归

日期：2026-09-24。工作分支：`master`。验证环境：macOS，本机 Chromium，构建后的离线 Web 静态资源；业务 API 响应由各 Playwright 场景模拟。

将原来分散的 18 个 `*_browser.py` 场景纳入 `pnpm test:browser`：脚本在 `127.0.0.1` 随机端口提供构建产物，逐项运行；失败立即返回非零退出码。运行前须 `pnpm build`，并预装 Python Playwright 与 Chromium。`browser_smoke.py` 依赖真实业务 API，因此不纳入这组模拟回归。

验证命令与结果：

- `cd business-web && pnpm test && pnpm build`：11 项 Node 单测通过，构建 8 个离线资源。
- `cd business-web && pnpm test:browser`：18 项 Chromium 场景全部通过，涵盖上传/失败重试、文档发现、复核决定与成果、模板、检索/审计续页、证据链接、原文格式与完整性、断线恢复、键盘焦点和异步写入/导航竞态。
- `.venv/bin/python -m ruff check business-web/scripts/run_browser_tests.py`：通过。

此证据只说明离线前端关键交互可重复回归；模拟响应不能证明真实 API 集成、模型解析质量或麒麟/NVIDIA/物理隔离部署。真实 API 冒烟、四类业务样本和目标机验收仍分别保留在后续门槛。回滚仅需撤销本次测试入口与文档变更，不涉及业务数据。
