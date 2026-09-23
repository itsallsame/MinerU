# P3 冻结证据只读契约

日期：2026-09-23；源码基线：`001efc30` 后的工作树。环境：Mac 开发机、隔离业务 SQLite 与本机 Doclib；不代表麒麟/NVIDIA/真实模型质量验收。

## 已实现

- 业务库可按业务文档列出解析修订、按修订列出冻结证据。开放 API 暴露 `GET /api/business/documents/{id}/revisions`、`GET /api/business/revisions/{id}/evidence` 和 `GET /api/business/evidence/{id}`；前端与自有 Skill 将共用这些接口。修订响应不返回 Doclib 内部 parse ID，证据响应不返回源文件存储键或宿主路径。
- 单条证据读取永远保留数据库中的 `snippet`、定位器、页/块、可用 bbox 和片段哈希。`EvidenceReader` 仅将当前 Doclib `read_content` 用作导航核验：相同源哈希、tier、请求定位器且全文片段哈希一致为 `current_match`；内容/身份不符为 `changed`；Doclib 断线或返回截断内容为 `unavailable`。这三个状态不是“历史解析版本真实性”的证明。
- 证据快照跨业务进程重建仍可列出；Doclib 不可用时冻结内容可读，不将实时失败误写成历史证据删除。

## 验证

- `.venv/bin/python -m pytest tests/business -q`：**55 passed，2 warnings**，在允许本机临时 Unix/TCP socket 的环境中执行。包含真实 Doclib HTML 解析→冻结证据→当前定位器匹配，以及内容变化、源哈希变化、截断、Doclib 断线和开放 API 的单元/契约检查。
- `ruff check mineru/business tests/business` 与 `ruff format --check mineru/business tests/business`、`git diff --check` 通过；测试没有真实业务文档或模型推理。

## 未完成

- 后续已增加按 parse ID 读取的公开 Doclib 接口和服务端证据捕获（见 `2026-09-23-p3-versioned-evidence.md`）；此处保留首次只读契约的原始验证记录。即使当前内容哈希相同，`current_match` 仍不代表历史版本身份。
- 模板/字段候选生成、人工复核、质量问题、成果状态及自有 Skill 仍待做。
- 列表端点尚未做分页和高并发容量验证；在真实规模下验收前需补限额、索引/性能及故障注入。
