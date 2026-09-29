# Downloads 现有公开 PDF 的 Mac 本机解析与业务 API 冒烟

日期：2026-09-29。环境：macOS，本 fork `master` 基线 `ae688607`，续测基线 `ab816f94`（测试/文档改动随本次提交），项目 `.venv`，MinerU 结果元数据报告 `4.0.6`。样本只从 `~/Downloads` 读取；测试在本机临时目录保存解析/业务状态，未调用 `--remote`，设置 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`、`HF_DATASETS_OFFLINE=1`。原文、结构化全文及人工标注均**未提交 Git**。

| 候选 | 文件 SHA-256 / 物理页数 | 本次结果 | 仍不能证明 |
| --- | --- | --- | --- |
| `~/Downloads/paper.pdf`，公开英文论文 | `4d48478dc0b6222d9f74d7db10ee776449b1209eb112632336544d32a49db97f` / 88 页 | CLI `flash` 前 3 页生成 3 页、11 个块，标题块与首页可见标题一致；完整文件经真实本机 Doclib＋业务 API 上传、任务完成、生成单个 Flash 修订，读出第一页历史文本与原生结构：续测 1 passed | 全文结构/字段质量、OCR/VLM 档位或四类业务准确率 |
| `~/Downloads/创新研报｜2024大模型训练数据白皮书_阿里研究院.pdf`，公开研究白皮书 | `c724288ae682ca6ba23b14cae9bd9301e4cd0e3425fc15be056e8c12d5a77a43` / 32 页 | CLI `flash --ocr-mode txt` 前 3 页生成 3 页、16 个块；首页为图像封面，输出含可见标题；完整文件经真实本机 Doclib＋业务 API 上传、任务完成，读出第一页历史文本与原生结构：续测 1 passed | 默认 `auto` 路径先前因本机沙箱拒绝写模型锁而失败；本次业务 Flash 联通不构成 OCR 模型推理或质量证据 |
| `~/Downloads/rmrb2023121903.pdf`，公开报纸版面 | `5cb6456d9b1afb7bf6d5159aecb3eb484be1624294520806d5abbbf1b59786c6` / 1 页 | CLI `flash --ocr-mode txt` 生成 1 页、62 个块；真实本机 Doclib＋业务 API 上传、任务完成、单个修订、历史文本与原生结构读取：续测 1 passed | 报纸多栏文章切分/标题层级质量。人工目视发现一段大段正文被标为 `paragraph_title`，说明“可解析”不等于结构正确 |

CLI 命令模式：

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 \
  .venv/bin/mineru-kit parse '/Users/ahaha/Downloads/<候选文件>.pdf' \
  --output /private/tmp/<本次新建目录>/<候选输出> --tier flash --pages 1-3 \
  --format middle_json --ocr-mode txt
```

论文的 CLI 测试使用默认 `ocr-mode=auto`；报纸只取第 1 页。白皮书 `auto` 因模型锁写入权限失败，随后明确指定 `txt` 文本路径重跑才通过；两种路径不能混称为 OCR 成功。业务 API 测试为 `tests/business/test_doclib_contract.py::test_local_pdf_through_real_business_api_and_doclib`，需显式设置 `MINERU_LOCAL_SAMPLE_PDF`，在未设置时跳过：

```bash
MINERU_LOCAL_SAMPLE_PDF='/Users/ahaha/Downloads/rmrb2023121903.pdf' \
  .venv/bin/python -m pytest -q -o addopts='' \
  tests/business/test_doclib_contract.py::test_local_pdf_through_real_business_api_and_doclib --tb=short
```

2026-09-29 续测将测试扩展为还读取第一页原生结构，并验证业务文档/修订归属、页码、非空块和机器未确认状态。上述三份各 1 passed、合计 3 passed；每次各有 2 条第三方依赖警告。续测白皮书的第一次受限沙箱运行仍因 Doclib 本机 Unix socket 创建被拒绝而在夹具设置阶段报错；获得本机测试权限后同命令通过。此前夹具漏接 `BusinessDiscovery` 导致历史读取 503，已在 `ab816f94` 修正。这些是测试环境/夹具问题，不是运行时解析修复。原有套件默认不读取 Downloads，且本次没有在浏览器中操作这三份文件。

这些 PDF 能推进**真实文件字节的联通验证**，但不是“公文、论文、研究报告、报纸各有独立人工标注”的黄金验收集。当前没有从 Downloads 选用可能涉及个人/案件的通知书，也没有把公文写作范例书冒充真实公文。没有预先锁定字段/结构标注和阈值，不能据此计算字段准确率、复核成本或宣称 QA-007–022 完成。报纸已观察到的 `paragraph_title` 误判可用于方案 B 的人工结构修正用例，但尚未执行写入。麒麟＋NVIDIA、物理断网、模型四档与完整 OCR 仍由实施团队现场验证。
