# QA-005 前端关键流程回归（Mac）

使用 opt-in `tests/business/test_live_web_smoke.py` 启动真实本地 Doclib、同源开放业务 Web/API 与 Playwright Chromium。原有真实 PDF、DOCX/PPTX/XLSX 上传、Flash 解析及历史页读取继续验证；本轮把临时 DOCX 增加明确的 `题目：MinerUOfficeDocxMarker` 行，并通过 Web 选择论文模板。

新增浏览器断言：DOCX 提取出带冻结证据的题目机器候选；在没有复核决定时，“确认并生成不可变成果版本”保持禁用；显式点击“接受候选”后出现已复核决定，确认按钮才可用；生成 `确认成果 v1` 后，JSON 下载中的字段值与原文标记一致；刷新页面并重新打开该文档仍能读取 v1。全程未拦截业务 API 路由，也未直接写业务数据库。PPTX/XLSX 仍按原测试检查 Flash 解析文本和浏览器不承诺内联原文预览。

验证（2026-09-24，Mac）：

- `MINERU_RUN_LIVE_BROWSER=1 .venv/bin/python -m pytest -q tests/business/test_live_web_smoke.py --tb=short`：**1 passed**；同一脚本使用真实 Web/API/Doclib，无业务路由 mock。
- `cd business-web && pnpm test`：**11 passed**；`pnpm build`：**8 个离线 Web 资源**生成。
- `.venv/bin/python -m pytest tests/business`：**176 passed、1 个 opt-in 跳过**；`.venv/bin/python -m pytest tests/unittest`：**2798 passed、4 skipped**。两套全量命令均在显式 opt-in 浏览器测试之外运行，不能把默认跳过计为已执行。
- 两个变更的 Python 测试脚本通过 Ruff lint/format 检查。

判定：QA-005 的 **Mac 前端关键流程回归通过**。这里的文档内容是合成 DOCX 和仓库公开 PDF，不是公文、论文、研究报告、报纸四类带人工标注的真实样本；不证明字段准确率、复杂版面质量、模型/GPU 档位或麒麟 x86_64 完全断网部署。系统仍为无登录、无用户或权限管理的开放业务 API，实际网络可达范围必须在目标环境验收。
