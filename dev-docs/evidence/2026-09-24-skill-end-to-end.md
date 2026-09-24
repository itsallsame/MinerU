# SKL-021：自有 Skill 本地端到端回归

在当前 fork 的 Mac 环境中，以独立 Skill CLI 进程连接临时启动的真实业务 API 和 Doclib TCP 服务，使用仓库公开 PDF 完成：显式确认后上传 → 业务任务轮询至解析完成 → 概览取得权威修订 `short_id` → 历史第一页定位读取 → 显式确认后发起提取 → 提取运行轮询至完成 → 候选仍标记未确认、已确认成果仍为空。该流程不调用官方 Skill、Doclib 内部接口、远程解析或模型服务。相同 opt-in 测试还验证 Web 对合成 DOCX 的人工复核成果与 Skill 读取的完整结果一致。

验证（2026-09-24，Mac，当前 P5 代码）：

- `MINERU_RUN_LIVE_BROWSER=1 .venv/bin/python -m pytest -q tests/business/test_live_web_smoke.py --tb=short`：1 通过；真实 Web/API/Doclib/Skill 流程，无业务 API 路由模拟。
- `.venv/bin/python -m pytest tests/business/test_skill_contract.py`：8 通过；覆盖连接边界、上传预检、错误、未确认/已确认状态、渐进式读取和写操作确认。

判定：`SKL-021` 的 Mac 本地端到端回归完成。它不证明麒麟 x86_64/NVIDIA 完全断网运行、真实四类标注样本质量、VLM 档位或长文档性能；这些属于独立 P1/P6 验收门禁。
