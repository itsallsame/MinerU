# QA-006 自有 Skill 真实业务链路回归（Mac）

在现有 opt-in `tests/business/test_live_web_smoke.py` 中，使用仓库公开 PDF 样本，启动**真实**本地 Doclib TCP 进程和同源业务 Web/API 进程。浏览器先对 PDF 与合成 DOCX/PPTX/XLSX 完成上传、Flash 解析和历史读取；随后以独立的 `skills/business-documents/scripts/business_documents.py` 命令行进程走同一个开放业务 API，不直连 Doclib、模型服务或远程解析服务。

Skill 断言链：上传 PDF 并绑定论文模板 → 用返回的业务任务 ID 轮询至完成 → `overview` 读取完成修订，未出现凭空的候选或确认结果 → 用修订响应的 `short_id`/`tier` 组成第一页定位器，经 `read` 读取历史内容，状态仍为 `historical_parse_unconfirmed` → 发起字段提取并轮询至完成 → `extraction` 显示 `machine_unconfirmed` → `results` 仍为空，不自动产生人工确认成果。

首次扩展测试时，测试自身错误地把源文件 SHA-256 前缀当成 Doclib `short_id`，业务 API 正确返回 `invalid_content_locator`（422）。改用**修订响应中的权威 `short_id`** 后通过；这一规则也写入自有 Skill 说明，避免 Agent 拼接错误定位器。没有因该失败修改生产解析逻辑。

验证（2026-09-24，Mac）：

- `MINERU_RUN_LIVE_BROWSER=1 .venv/bin/python -m pytest -q tests/business/test_live_web_smoke.py --tb=short`：最终扩展后 **1 passed**；服务与浏览器用完即退出，文件仅在临时目录。
- `.venv/bin/python -m pytest tests/business`：**176 passed、1 个 opt-in 跳过**；`.venv/bin/python -m pytest tests/unittest`：**2798 passed、4 skipped**。这两条清单原命令在同轮、最后扩展提取断言之前通过；之后变动仅为 opt-in 测试与 Skill 说明。
- `.venv/bin/python -m pytest -q tests/business/test_skill_contract.py`：**6 passed**；Ruff 检查和格式检查通过。

判定：QA-006 的 **Mac 自有 Skill 端到端回归通过**，覆盖真实同源业务 API/Doclib、任务、历史读取、提取和“未确认不冒充确认”的边界。它不等于麒麟 x86_64/NVIDIA 完全断网验收，也不证明真实四类标注样本的字段准确率、PDF/Office 布局质量或人工复核流程已完成。系统仍无用户、登录或权限管理，生产可达范围需由物理隔离和部署网络控制。
