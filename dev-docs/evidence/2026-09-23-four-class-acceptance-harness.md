# 四类真实样本验收工具的契约证据（尚非真实样本验收）

日期：2026-09-23。源码：当前 fork 的 `master`，本轮提交见 Git 日志。运行环境：Mac 开发机、Python 3.13。

## 已实现

- `scripts/evaluate_business_samples.py` 只读访问开放业务 API，要求四类脱敏真实原文、每份源文件哈希、明确的业务文档/修订/提取运行 ID 与人工字段标注。身份或状态不符时拒绝输出总报告；不自动上传、解析、提取、冻结、确认或访问 Doclib 私有接口。
- 分别统计精确字段召回、候选精确率、冻结证据命中、可选标题召回与未处理阻断问题；总计、四类和手写/跨页表格/印章水印标签分组。报告只保留非敏感样本编号与计数/比率，不回显原文、人工标注值或冻结片段；空分母为 `null`。
- 输入格式、隔离目录和解释边界见 [real-sample-acceptance.md](../real-sample-acceptance.md)。

## 验证

- `ruff check scripts/evaluate_business_samples.py tests/business/test_sample_evaluation.py` 通过。
- `pytest -q --no-cov tests/business/test_sample_evaluation.py`：5 项通过；覆盖四类齐全、源文件篡改拒绝、身份不符拒绝、证据页码不符时不计命中、报告不含原文、真实 FastAPI/业务库合成提取记录读取。
- `pytest -q --no-cov tests/business`：118 项通过，2 条依赖弃用警告。
- `scripts/evaluate_business_samples.py --help` 可正常显示显式的 `--suite`、`--base-url`、`--environment`、`--output` 参数。

## 未完成

仓库内未发现公文、论文、研究报告、报纸四类**带人工标注的脱敏真实业务原文**。上述测试数据均是合成夹具，没有得出真实字段准确率、模型结构质量、复核工作量或任何达标结论。麒麟 x86_64 + NVIDIA 完全断网环境和模型组合仍未验收；QA-007 至 QA-022 等真实样本与目标环境条目保持未通过。
