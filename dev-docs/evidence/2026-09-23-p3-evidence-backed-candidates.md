# P3 历史解析证据绑定的字段候选（2026-09-23）

## 实现契约

- 新 `extraction_runs` 绑定业务解析修订与文档创建时的不可变模板版本；`field_candidates` 的每条记录必须引用同一解析修订的冻结 `evidence`，且候选值实际出现在冻结片段内。
- Doclib 读取路径是 `get_parse(parse_id)` 与 `read_parse_content(parse_id, page_locator)`；拒绝源 SHA-256、short ID、tier 不符、未完成解析、页面截断或历史批次不可用。不会退回当前最新解析。
- 当前候选方法仅为显式标签行规则 `label_rule`，不声明模型置信度、不确认结果；多值冲突和完整覆盖下的必填缺失产生阻断问题。部分页解析只生成 `coverage_incomplete`，不制造“字段缺失”假结论。
- 开放业务 API：`POST /api/business/revisions/{id}/extractions`、`GET /api/business/revisions/{id}/extractions`、`GET /api/business/extractions/{id}`；不泄漏 Doclib parse ID、内部路径或用户身份。失败运行留状态和错误码，但 API 不展示部分候选。
- SQLite 原型 schema 4；前几个 schema 不迁移也不删除，原型运行需新空库。

## 验证

- 定向测试覆盖模板版本冻结、原文证据一致性、必填缺失、候选冲突、部分页覆盖、失败运行和开放 API。
- 实际 Doclib HTML 集成测试强制二次解析并改写当前批次后，旧修订的提取仍得到旧标题，而当前定位器读取新标题；证明提取未静默切换到最新批次。
- 完整 `tests/business`：**73 passed、2 warnings**（49.93 秒，Mac 本地 socket 已允许）。Ruff 检查通过。

## 尚未达标

- 当前同步请求每个解析批次最多扫描 1000 页、每页读取上限 30000 字符；超限失败，不假称扫描完整。生产需要持久异步队列、分页/批次续跑和性能验收。
- 字段规则仅识别明确标签行，尚未完成复杂版式、表格、作者列表、日期标准化、模型辅助候选、置信标定或真实样本准确率评估。
- 尚无人工复核/问题处理/成果确认状态机；任何候选都不能作为已确认成果。失败运行内部可能留有部分证据/候选，API 不返回其结果，清理/重试策略后续补齐。
