# P4 证据与复核工作台阶段验证

日期：2026-09-23。环境：Mac Apple Silicon、Python 3.13/3.14、Node 24、Playwright Chromium；未在麒麟/NVIDIA 目标机运行。业务 API/Web 同源启动于本机临时端口 18088；浏览器测试对文档/复核记录使用合成拦截数据，真实模板和静态页面来自当前构建。

## 实现范围

- 选择解析修订和字段提取运行；提取排队/运行中不展示候选，失败不展示部分结果。运行结束后读取上传时冻结的模板版本、候选、问题、决定、结果和审计。
- 基于历史修订按页采集冻结证据，查看冻结片段与当前定位状态；只有 `current_match` 且存在 PDF 页码时给出“尝试跳转”入口。`changed`/`unavailable` 继续保留冻结片段，禁用跳转。
- 机器候选保持“未确认”状态。接受候选或人工修订都写入显式复核决定；问题处理附原因。未处理问题、必填字段无决定、与上一确认版本无新决定时禁用确认。
- 已确认成果按不可变版本展示；JSON/Markdown 仅从确认结果生成，不把候选或未复核草稿混入。操作记录只显示 `web`/`skill` 等来源，不虚构用户身份。本系统无登录、用户、角色或权限管理。

## 验证

- `pnpm --dir business-web test`：7/7 通过；`pnpm --dir business-web build`：7 个离线静态资源构建，包含新的复核模块。
- `.venv/bin/ruff check business-web/tests/review_browser.py` 与 `git diff --check` 通过。
- `.venv/bin/python -m pytest -q tests/business`：88/88 通过；2 个依赖弃用警告，与本次页面变更无关。
- `/opt/homebrew/opt/python@3.14/bin/python3.14 business-web/tests/review_browser.py http://127.0.0.1:18088 dev-docs/evidence/screenshots/p4-review-desktop.png`：通过。覆盖冻结片段、当前 PDF 页跳转、定位变化后禁用跳转、候选接受、问题阻断和处理、确认版本、禁止无变化重确认、JSON/Markdown 下载及 390px 宽度无横向溢出。
- 原有 `business-web/tests/browser_smoke.py` 回归通过，覆盖开放入口、上传格式/档位、列表筛选、文档详情及窄屏布局。合成页面截图：[复核工作台](screenshots/p4-review-desktop.png)。

## 未完成与风险

- 浏览器脚本拦截业务记录，**不能证明真实 Doclib/模型解析、证据采集、API 持久化或麒麟 GPU 端到端流程**；真实文档样本和目标机门槛仍开放。
- PDF 浏览器页跳转依赖浏览器内置 PDF 查看器，状态为 `current_match` 只说明当前定位内容匹配冻结片段，不证明仍是同一历史解析批次。图片/Office 未承诺页级跳转。
- 搜索/结构树、字段高亮/双向联动、修订差异、模板管理与质量统计尚未完成；P4 未完成。当前操作来源不是人员身份，开放系统只能靠物理隔离与网络边界控制访问。
