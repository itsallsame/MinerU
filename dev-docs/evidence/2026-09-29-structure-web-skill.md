# FE-033 B：Web 与自有 Skill 共用派生结构 API

日期：2026-09-29。环境：Mac 开发机，MinerU 4.0 fork，业务 schema 19。此关口不涉及麒麟/NVIDIA 目标机部署。

## 本次交付

- Web 复核页按业务修订和页码读取 `/structure/derived`，明确区分原生机器 v0 与 `human_edited_structure` v1+；列出稳定节点 ID、来源路径和内容是否已变更。选择节点时，仅在单一原生来源且无覆盖文本时调用 `/structure/node-text` 取得完整文本，不能把 200 字预览当成全文。
- Web 提供 `set_type`、`set_text`、`split`、`merge`、`move`、`insert`、`delete` 七种有限操作的表单；附人工原因与当前版本号。每次 POST 前把随机请求键和意图保存在同源浏览器存储。未知结果只 GET 原请求键；本页有待核对请求时暂停新编辑，同键重试需再次明确确认。409 会刷新当前页，不自动合并。
- 自有 `skills/business-documents` 增加 `structure-derived`、`structure-node-text`、`structure-edit`、`structure-request` 命令。写入需 `--confirm-write`，传 `source=skill`、理由、预期版本和请求键；HTTP 408/5xx/断连或不完整成功响应时只查原键，不自动再次 POST。Skill 文档明确人工授权、原始锚点与非确认成果边界。
- 两个入口都仅调用同一 `/api/business`，不触碰 Doclib 原生树、不接官方 Skill 或模型服务；仍无用户/权限管理。

## 验证记录

- `tests/business/test_skill_contract.py`：21 passed。新增 Skill 读全文/派生页、显式写入 JSON 与来源、回执查询、503 后原键 GET 且仅一次 POST、缺少确认标志拒绝写入。
- Web Node 测试：13 passed；`npm run build`：8 个离线资源构建成功；JS 语法检查通过。
- Playwright 离线 `review_browser.py`：写入 v0→v1、重读 v1、409 冲突刷新、503 未知结果同键回查 v2，以及 503+404 后刷新页面仍保留待核对请求、明确同键重试到 v3 均通过；原有证据/字段确认流程仍通过。最终完整 29 项 Chromium 回归通过。此前扩展用例后的某次运行中，与结构修改无直接关联的 `review_request_unknown_browser.py` 曾出现一次 30 秒等待超时；单独重跑及最终整套重跑均通过，仍记录这次间歇现象。
- Ruff 检查、Skill `quick_validate.py`、JSON 解析和 `git diff --check` 通过。

## 未完成与边界

当前 `/structure/derived` 是版本读取，不是独立的人工结构导出格式；下一步须定义包含修订/页/版本及 `human_edited_structure` 状态的导出，不能覆盖历史 Markdown、证据或已确认字段成果。Web 操作表单与合成用例证明链路可用，不证明对报纸多栏、错字或复杂层级的人工修正质量。之前公开 Downloads 报纸 PDF 的真实 Doclib/API v0→v1 检查见[后端证据](2026-09-29-structure-versioned-operations.md)；本关口没有新增真实样本人工标注或麒麟/GPU 验收，FE-033 继续保持未完成。
