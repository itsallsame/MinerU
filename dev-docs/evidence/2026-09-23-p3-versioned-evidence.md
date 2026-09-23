# P3 解析修订绑定证据原型

日期：2026-09-23；环境：Mac 开发机、隔离本机 Doclib 与业务 SQLite。目标麒麟 x86_64/NVIDIA、真实模型与断网容器尚未验收。

## 决策与实现

- Doclib 新增公开 SDK/HTTP 契约 `read_parse_content(parse_id, locator)`，读取指定解析批次的持久 JSON；缺失、未完成或定位器与解析来源不符时失败，绝不改读较新批次。现有 `read_content(locator)` 仍按当前内容工作，两者语义不同。业务 API 不直接暴露 Doclib 的内部端口或 parse ID。
- 业务 API 新增 `POST /api/business/revisions/{id}/evidence`，请求体仅含 `locator`，额外 `snippet` 被 422 拒绝。服务端读取业务修订保存的 parse ID，核对完整源哈希、short ID、tier、定位器和非截断文本后冻结证据；业务库旧证据不随重解析变动。
- 发现 Doclib 默认每小时压缩可能删除旧解析批次和 ID，因此业务 Compose 设置 `MINERU_DOCLIB_COMPACTION_INTERVAL_SEC=0`，Doclib 在间隔为 0 时不创建压缩后台任务。此举增加磁盘占用，目标机需规定容量与备份/清理策略。批次 JSON 创建改为排他写入并在毫秒时间戳碰撞时递增，避免新解析覆盖旧文件。

## 可复现验证

- 本机 HTML 文档完成两次强制解析后，仅在**测试临时目录**把第二批次持久 JSON 中的文本改为不同内容：旧 parse ID 读取仍是原文字，新 parse ID 与普通当前定位器读取变成新文字；业务 `EvidenceWriter` 对旧修订冻结原文字。错误 parse ID 与不匹配定位器明确失败。未修改任何真实业务数据或模型文件。
- `.venv/bin/python -m pytest tests/business -q`：**58 passed，2 warnings**；相关 Doclib 接口/渐进读取/路由/缓存单测：**251 passed**；完整 `.venv/bin/python -m pytest tests/unittest -q -o addopts='' --tb=short`：**2798 passed、4 skipped、2 warnings**。单独测试验证零间隔不启动压缩任务和相同毫秒下两个批次文件不互相覆盖。
- `ruff check` 对生产代码、业务测试、新批次文件测试和接口契约测试通过；原有 `test_doclib_app_startup.py` 全文件已有 50 项类型注解 lint 债务，本轮只对该文件运行 `--select E,F,W,C` 并通过，未把旧债务伪称全绿。`ruff format --check`、`git diff --check` 和占位变量下的 Compose 语法检查通过。测试中的两条警告来自当前 Starlette/httpx/anyio 组合。

## 未完成与边界

- 禁用自动压缩不等于永久保存：手动清理、源文件丢失、Doclib 目录损坏或管理员操作仍可使历史解析文件不可读；已冻结片段可继续展示，但重新按 parse ID 读取应失败。业务库/Doclib/源文件一致备份及恢复、磁盘满、并发解析、较大 PDF 分批等需要现场和故障测试。
- 字段候选自动提取、证据 bbox 来源、质量问题、人工复核、成果版本和 Skill 尚未接上这个捕获流程。当前只证明服务端从指定解析修订捕获原文，不证明业务字段准确率或历史解析永不丢失。
