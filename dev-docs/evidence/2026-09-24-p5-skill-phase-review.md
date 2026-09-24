# P5 自有 Skill 阶段审查

日期：2026-09-24。审查对象：`skills/business-documents/SKILL.md`、`skills/business-documents/scripts/business_documents.py`、业务 API 契约、`tests/business/test_skill_contract.py` 和真实本地联通测试。`feature-list.json` 的 P5 为 22/22；这仅表示 Skill 开发阶段的 Mac 契约/本地联通门禁，不代替 P1/P6 的麒麟、物理隔离与真实样本验收。

## 设计一致性逐项核对

| 功能 | 现有实现与证据 | 结论 |
| --- | --- | --- |
| SKL-001 自有元信息 | 独立 `business-documents` frontmatter；不引用官方 Skill 入口 | 一致 |
| SKL-002 安装说明 | Skill 文档说明准备机安装/隔离区批准介质导入 | 一致 |
| SKL-003 API 配置 | `MINERU_BUSINESS_API_URL`/`--base-url` 明确指定 | 一致 |
| SKL-004 开放地址边界 | 只接纳批准私网/回环解析地址；明确不等于认证或物理隔离 | 一致 |
| SKL-005 文档提交 | `upload` 使用业务 API、能力预检与显式写确认 | 一致 |
| SKL-006 任务状态 | `task` 查询业务任务，不把提交当完成 | 一致 |
| SKL-007 字段候选 | `extraction`/`overview.draft` 保留机器未确认标记 | 一致 |
| SKL-008 质量问题 | `extraction` 返回问题，Skill 文档要求报告未解决问题 | 一致 |
| SKL-009 业务搜索 | `search`/修订内搜索调用业务 API，预览不作为冻结证据 | 一致 |
| SKL-010 定位读取 | `read` 固定修订并使用返回的 locator/next_locator | 一致 |
| SKL-011 已确认成果 | `results`/`result` 仅读取成果 API | 一致 |
| SKL-012 草稿标记 | `machine_unconfirmed` 与 `confirmed` 明确分开 | 一致 |
| SKL-013 证据链接 | 同源业务 Web `#evidence` 深链与导航状态说明 | 一致 |
| SKL-014 不存在/不可达错误 | 状态码、服务错误和无法连接均显式输出 | 一致 |
| SKL-015 不直连原始 API | 客户端请求统一加 `/api/business`；无 Doclib/V1/模型调用 | 一致 |
| SKL-016 禁止自动确认 | CLI 没有字段决定、问题处理或确认成果命令 | 一致 |
| SKL-017 写流程确认 | `upload`/`extract` 需 `--confirm-write`，未携带前无业务请求 | 一致 |
| SKL-018 无远程上传 | 只允许批准私网/回环地址；无云端解析回退 | 一致，仍依赖部署隔离 |
| SKL-019 错误解释 | 404/409/503 与机器状态的说明和测试 | 一致 |
| SKL-020 渐进阅读 | 固定修订的窗口续读示例和 `next_page` 契约测试 | 一致 |
| SKL-021 端到端 | 真实本地 Skill→业务 API→Doclib PDF 流程 | 一致 |
| SKL-022 Web/Skill 一致 | 同一已确认成果的 Web JSON 与 Skill 对象相等 | 一致 |

复用边界：Skill 只有一个标准库 `BusinessClient`，所有命令通过该客户端访问与 Web 相同的开放业务 API；没有重复 Doclib 网关、模型调用或第二套成果状态。审查时发现非全球地址仍可能不属于批准私网，以及主机名 DNS 回答未核验；已在 `dd437b05` 收紧网段并连接到校验后的数值地址，契约测试覆盖混合私网/公网 DNS。原计划中“结构树和页块检索仍待补齐”的过时说明已同步修正。

验证（Mac，审查修复后）：

- `.venv/bin/python -m pytest tests/business/test_skill_contract.py`：9 通过。
- `PYTHONPATH=. .venv/bin/pytest -q tests/business`：189 通过、1 个 opt-in 跳过。
- `MINERU_RUN_LIVE_BROWSER=1 .venv/bin/python -m pytest -q tests/business/test_live_web_smoke.py --tb=short`：1 通过。
- `.venv/bin/python -m pytest -q tests/unittest --tb=short`：2798 通过、4 跳过。测试输出中的 CLI 错误信息是预期反例断言，进程最终为 0。
- Ruff lint、`git diff --check` 和特性计数检查通过。

风险与后续：没有专用 Codex CLI review 工具可调用，本次按执行规范作逐文件人工审查，不冒充自动审查。`--confirm-write` 是 Agent 侧流程检查而非服务端授权；私网地址校验不证明物理隔离或目标服务真实性。Mac 测试未验证麒麟 x86_64/NVIDIA、真实权重、四类标注样本、长文档吞吐或断网部署。这些 P1/P6 门禁保持未完成；全项目 Goal 仍在进行。
