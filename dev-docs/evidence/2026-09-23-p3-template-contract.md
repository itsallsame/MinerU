# P3 模板契约阶段记录（2026-09-23）

## 已完成

- 新业务域定义四类默认模板和字段类型 `text/date/list`，不复制 V1 代码、数据库或兼容契约。
- 业务 SQLite schema 2 采用 `templates` 当前指针和 `template_versions` 不可变版本表；新空库一次性种入四种默认模板。旧 schema 1 直接拒绝，既不迁移也不删除。
- 开放业务 API：列出、新建、按版本读取、更新与停用自定义模板；没有用户、登录、角色、所有者或权限判断。
- 内置模板禁止原地编辑、停用；自定义模板字段代码唯一、类型与名称校验，并保留旧版。

## 验证与限制

- `tests/business/test_templates.py` 与 `test_business_store.py`：10 passed；Ruff checks passed。
- 全部 `tests/business` 在允许本地 Unix/TCP socket 的环境中重跑为 **62 passed、2 warnings**（45.27 秒）。首次受限沙箱运行的 1 failed、15 errors 均由系统拒绝监听本地 socket 造成；已被完整重跑结果替代。
- 未实现文档绑定模板版本、字段候选、质量问题、人工复核、确认成果；P3 阶段未完成。这里的“必填”仅是模板定义，不等于已执行质量检查。
- 缺少真实脱敏样本与麒麟目标机验证，未声称解析/抽取准确率。
