# 四类真实业务样本验收输入与只读评估

状态：评估工具与合成契约测试已就绪；**尚无四类脱敏原文和人工标注，准确率与生产验收未执行**。这份说明不是对模型质量的预测。

仓库公共 `demo/pdfs/demo1.pdf` 及临时生成的 DOCX/PPTX/XLSX 已用于 Mac 的真实 Web→业务 API→Doclib Flash 解析/历史页读取冒烟（见 [PDF 联通证据](evidence/2026-09-24-live-web-pdf-smoke.md)与 [Office 联通证据](evidence/2026-09-24-live-web-office-smoke.md)）。这些都没有四类业务人工标注，也不使用 NVIDIA/VLM 模型；不能纳入下述四类准确率或生产验收分母。

## 样本准备

在仓库外的受控目录放置公文、论文、研究报告、报纸各至少一份脱敏**真实**原文及 `suite.json`。不得把原文、人工标注、业务 API 返回正文或含敏感内容的运行日志提交到 Git。`suite.json` 的 `schema` 固定为 `1`，`cases` 为数组；每个 case 必须有：

| 字段 | 要求 |
| --- | --- |
| `id` | 无敏感内容的唯一样本编号；会出现在脱敏指标报告 |
| `category` | `official_document`、`paper`、`research_report`、`newspaper` 之一；四类必须齐全 |
| `source`、`sha256` | 相对 `suite.json` 所在目录的原文路径和该原文小写 SHA-256；评估前逐文件复核 |
| `document_id`、`revision_id`、`run_id` | 在同一开放业务 API 上完成上传、解析和提取后，明确记录要评估的业务 ID；工具不会上传或启动任务 |
| `expected_fields` | 非空人工标注数组；每项含 `code`、精确 `value`，可加 `evidence_quote` 与 `page_no` 校验证据位置 |
| `expected_headings` | 可选的人工标题数组；每项含 `title`、`level`（1–6）、`page_no`；给出时扫描完整历史修订目录 |
| `tags` | 可选：`handwritten`、`cross_page_table`、`seal_watermark`；用于专项分组，不代表已覆盖该场景 |

示例 case 结构如下（仅说明字段，不能当成真实验收数据）：

```json
{
  "id": "case-001",
  "category": "official_document",
  "source": "case-001.pdf",
  "sha256": "<原文文件的 64 位小写 SHA-256>",
  "document_id": "<业务文档 ID>",
  "revision_id": "<历史解析修订 ID>",
  "run_id": "<已完成的字段提取运行 ID>",
  "expected_fields": [
    {"code": "title", "value": "<人工标注标题>", "evidence_quote": "<原文片段>", "page_no": 1}
  ],
  "expected_headings": [{"title": "<人工标注标题>", "level": 1, "page_no": 1}],
  "tags": ["seal_watermark"]
}
```

## 运行与解释

先由人工通过业务 Web/API 将每份原文上传、等待解析和字段提取真正完成，记录对应 ID。随后在能访问受控样本目录和业务内网的机器上运行：

```bash
.venv/bin/python scripts/evaluate_business_samples.py \
  --suite /受控目录/suite.json \
  --base-url http://127.0.0.1:8088 \
  --environment 'Mac 开发验证；记录实际 MinerU 提交、模型和档位' \
  --output /独立受控报告目录/本次运行唯一名称.json
```

工具只发 GET 请求，不修改业务系统。先校验四类样本、源文件 SHA-256、业务文档/修订/提取运行身份与完成状态；任一不符则停止，不输出“部分通过”的总报告。报告采用原子新建：目标路径已有文件或符号链接时拒绝运行，并发写入时也不覆盖先写入的报告；每次运行请使用包含环境、模型版本和时间的唯一名称。它统计精确字段值召回、候选精确率、证据快照命中、可选标题召回以及未解决阻断问题，分别给总计、各类和场景标签分组。缺少分母的比率为 `null`，不填猜测值。同字段同值存在多个机器候选或人工标注时，按一对一关系优先最大化有效证据命中，再匹配剩余同值候选；不能把同一个候选重复计分，也不能仅因先遇到无证据候选而低估命中。证据命中同时要求候选值确实在冻结片段中；若人工标注给出原文片段/页码，还须匹配它们。报告不包含原文、字段值或冻结证据文本；样本 ID 必须自行选用无敏感信息的编码。

这些指标只描述指定提取运行的**机器候选**，不代表人工确认后的成果准确率、全文相似度、表格结构质量、复核耗时、吞吐或最终生产通过。评估阈值需与业务方在看真实数据前明确，不能事后为了通过而调整。Mac 与物理隔离麒麟 x86_64 + NVIDIA 必须分别运行并记录源码提交、模型清单哈希、档位、运行环境、失败样本及已知限制；不能用 Mac 结果代替目标机验收。手写、跨页表格、印章/水印没有对应真实标注样本时，各专项继续标未完成。
