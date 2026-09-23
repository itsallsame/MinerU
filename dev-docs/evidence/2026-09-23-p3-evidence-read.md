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

- Doclib 公开 locator 不包含 parse ID。即使当前内容哈希相同，也不能断言来自原解析修订。若业务要求严格逐修订原文复现，需要增加可按 parse ID 读取的公开接口或在业务生成证据时额外冻结足够的原始块；先写失败契约测试再动 Doclib 核心。
- 目前证据捕获存储函数是内部原型，尚无服务端真实性约束和面向业务的字段候选生成流程；因此不开放客户端提交任意 `snippet` 的 API。人工复核、模板/字段、质量问题、成果状态及自有 Skill 仍待做。
- 列表端点尚未做分页和高并发容量验证；在真实规模下验收前需补限额、索引/性能及故障注入。
