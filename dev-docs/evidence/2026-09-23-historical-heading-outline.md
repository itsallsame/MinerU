# 历史解析标题目录验证（Mac）

日期：2026-09-23。范围：MinerU 4.0.6 fork 的开放业务 API、Web 与自有 Skill。此处是历史 Markdown 派生目录，不是 MinerU 原生块级结构树。

## 契约

`GET /api/business/revisions/{id}/outline` 只读取指定业务修订的历史 Doclib 批次，不从当前索引推断。识别 Markdown `#`–`######` 标题，排除代码围栏内的伪标题，返回级别、标题、页号与页定位器；每次最多 25 页，长文档必须沿 `next_page` 继续扫描。截断、历史批次缺失或身份不符时失败，不产生不完整的成功结果。Web 提供“读取标题目录→读取所在页”，Skill `outline` 使用同一业务 API。返回内容标为 `historical_parse_unconfirmed`。

## 验证

- 真实本地 Doclib 的 HTML H1 能形成一级标题；仓库 13 页 PDF 通过业务 API 扫描全部 13 页，目录响应不包含内部 parse ID/路径。该 PDF 测试不声称每页都有标题。
- 业务单元/契约测试验证两批次标题顺序、代码围栏排除、27 页续扫及截断失败。Skill 经真实 FastAPI ASGI 适配器读取目录，结构校验通过。
- 全量 `tests/business` 110 项通过（2 条依赖弃用警告）；Web 测试 9 项、8 个离线资产构建通过；Playwright 对实际构建包验证目录到页读取且原有复核/检索流程通过；Ruff 与 diff 检查通过。

## 未完成

Markdown 标题目录不等于官方模型块结构、表格/公式层级或块级证据；没有精确标题块定位，也不能保证没有标题就表示原文没有语义结构。FE-023 保持未完成。四类人工标注业务样本与麒麟 x86_64/NVIDIA 完全离线目标机仍未验收。
