# P3 业务身份与证据存储原型

日期：2026-09-23。环境：Mac、Python 3.13、MinerU fork 4.0.6、隔离临时文件；真实 Doclib 联调仅使用本机 Unix socket 与无模型 HTML Flash。**不是 P3 阶段完成报告，也不是麒麟现场验收。** P1/P2 现场门槛仍开放，P3 Mac 并行范围已写入阶段计划。

## 实现决定

- 初版业务库是与 Doclib 数据库分开的单机 SQLite，显式初始化、WAL、外键、单操作事务和版本 1 新 schema；不读取或迁移 V1 旧库。未知 schema 与符号链接库路径拒绝初始化；新建库文件权限为 `0600`，数据库目录由部署方预先创建并限制权限。
- `BusinessDocument.id` 是每次业务上传独立生成的不透明 ID；同内容可有多个业务文档与不同所有者。数据库只记原文件存储键，不把绝对路径当业务接口契约。
- 已完成的 Doclib `ParseInfo` 可登记为业务 `ParseRevision`，验证源 SHA、所有者和完成状态；同一文档/parse ID 重试返回原修订，冲突的生产者信息拒绝覆盖。重解析产生新的业务修订。
- `EvidenceSnapshot` 保存修订 ID、locator、页/块、可选 bbox、冻结片段及片段 SHA；定位器的 short ID/tier 必须与修订一致。存储层没有修改旧证据的方法，读操作按所有者过滤。

## 验证

`.venv/bin/python -m pytest tests/business/test_business_store.py -q`：**6 passed**；`.venv/bin/python -m pytest tests/business/test_doclib_contract.py::test_business_evidence_references_real_doclib_parse -q`：**1 passed**；`.venv/bin/python -m pytest tests/business -q`：**38 passed**。`ruff check` / `ruff format --check` 对新增业务代码通过。真实 Doclib 联调使用生成的 HTML：上传发布、解析完成、获取实际 ParseInfo/locator、写入业务修订及证据、从新 BusinessStore 实例读回。

## 尚未证明及后续风险

- 当前 `capture_evidence` 是内部存储原语，调用方提供片段；仅真实联调测试按 Doclib 当前内容读取。尚未有统一业务服务在保存前核验片段、权限与源解析的同时性。Doclib locator 不含 parse ID，若并发强制重解析，不能仅凭 locator 证明旧修订的原文；后续必须做业务侧解析/证据写入串行化与快照一致性测试。
- 尚无上传文件与业务库记录的跨资源事务、孤儿文件回收、任务队列、模板/字段/问题/复核/成果、API 鉴权和审计。当前所有者过滤不是完整角色/共享权限模型。
- SQLite 的并发容量、在线备份/WAL 一致性、恢复到麒麟及运维保留期未验证；不能据此承诺生产规模。
- 没有真实公文、论文、研究报告、报纸样本或模型驱动档位质量数据。

下一步：建立业务服务层，确保上传与业务记录失败时可恢复；再将 Doclib 读取、证据快照、业务权限统一放在服务/API 边界。保留 P1/P2/P6 现场门槛。
