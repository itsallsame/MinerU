# SKL-022：同一已确认成果的 Web/Skill 一致性

扩展现有 opt-in 本地端到端测试：浏览器在真实同源 Web/API/Doclib 上上传合成 DOCX，完成机器候选的显式接受和成果确认，下载 Web 的 JSON 成果。测试从下载对象取得 `run_id` 和 `id`，随后以独立自有 Skill CLI 进程调用同一业务服务的 `results RUN_ID` 与 `result RESULT_ID`，分别把完整结果对象与 Web 导出的 JSON 作相等断言。这样比较的是同一个成果版本，而非两个入口各自能调用 API 的间接证据。

验证（Mac）：

- `MINERU_RUN_LIVE_BROWSER=1 .venv/bin/python -m pytest -q tests/business/test_live_web_smoke.py --tb=short`：1 通过，包含 Web 人工复核、确认、JSON 下载、刷新后读取以及 Skill 同一成果逐字段相等比较。
- `.venv/bin/python -m pytest tests/business/test_skill_contract.py`：7 通过。
- Ruff lint 与 `git diff --check` 通过。

本测试使用仓库公开 PDF 和临时合成 Office 文件，不是四类真实标注样本；只证明 Mac 同一 API 的 Web/Skill 成果状态与值一致，不证明麒麟/NVIDIA、物理断网或模型质量。开放系统仍无用户和权限层。
