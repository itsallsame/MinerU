# QA-004 开放业务 API 契约回归（Mac）

本轮在 `tests/business/test_business_api.py` 加入公开接口目录回归：关键文档/任务/历史内容/提取/确认/结果/模板路径与 HTTP 方法必须保留；OpenAPI 的业务路由不能混入 Doclib 内部路径，顶层与每个操作均不得声明认证要求或安全方案。这把用户确认的“开放系统、无用户与权限管理”固化为自动化契约，但**不是**证明任何网络都可安全访问的安全测试。

现有业务测试分别覆盖上传、能力/源文件、任务状态与重试、模板版本、按业务修订的搜索/渐进读取/差异/结构、历史证据、机器候选、显式复核与成果版本、审计游标，以及自有 Skill 仅通过业务 API 调用。核心文件包括 `test_business_api.py`、`test_business_discovery.py`、`test_evidence_api.py`、`test_document_workflow.py`、`test_field_extraction.py`、`test_review_results.py`、`test_templates.py`、`test_skill_contract.py`。

验证（Mac，Python 3.13）：

- 新增后 `.venv/bin/python -m pytest tests/business --tb=short`：**176 passed、1 个 opt-in 跳过**，2 条依赖弃用警告；`--tb=short` 不改变测试范围。
- 最后增加顶层 `security` 断言后，`.venv/bin/python -m pytest -q tests/business/test_business_api.py`：**5 passed**。
- `.venv/bin/ruff check tests/business/test_business_api.py`：通过；完整上游 `tests/unittest` 在同一轮、仅文档与此业务测试变更前为 **2798 passed、4 skipped**（见 QA-003 记录）。

判定：QA-004 的 Mac API 契约回归通过。未证明目标麒麟机器的离线镜像、容器网络边界、模型/GPU 解析、四类真实标注样本质量，也未自动运行 opt-in 浏览器测试。生产开放 API 仅可在已确认的物理隔离与受控内网中暴露；Doclib 内部接口不应对业务网络发布。
