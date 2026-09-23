# P5 自有业务 Skill 第一组能力

日期：2026-09-23。源码基线：`475a0a26`。开发环境：Mac Apple Silicon、Python 3.13；目标麒麟/NVIDIA 尚未运行。

## 实现

- `skills/business-documents/SKILL.md` 明确这是 fork 业务平台的 Skill，不安装或调用官方 MinerU Skill；仅通过与 Web 相同的 `/api/business` 读取或提交。`scripts/business_documents.py` 使用 Python 标准库，不增加线上依赖或模型调用。
- 支持服务能力、模板、文档列表、文件上传、任务状态、解析修订、启动字段提取、读取机器候选/问题/冻结证据/确认成果，以及最新修订概览。上传先取服务能力并检查格式、大小和档位，以流式 multipart 传输文件；不访问 Doclib 或外部解析服务。
- 概览把 `draft.state=machine_unconfirmed` 与 `confirmed_for_latest_run` 分开，后者为空仅表示**最新修订的最新提取运行**尚无确认成果，不代表其他运行或旧修订没有确认成果。Skill 指令要求对候选标“未人工确认”，引用证据 ID/locator；证据 `changed`/`unavailable` 不声称可回跳历史原文。
- 脚本不提供字段决定、问题处理、确认成果命令；人工复核仍由业务 Web 流程完成。这是流程边界，不是权限认证。业务 API 本身开放，Skill 不创建用户、角色、令牌或个人审计身份。脚本要求显式配置内部 HTTP 地址，拒绝公网 IP 字面量和非内部主机名；此校验不能替代物理隔离或批准的内网路由。

## 验证

- `.venv/bin/python /Users/ahaha/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/business-documents`：Skill 格式与元信息校验通过。
- `.venv/bin/ruff check skills/business-documents/scripts/business_documents.py tests/business/test_skill_contract.py`：通过。
- `.venv/bin/python -m pytest -q tests/business/test_skill_contract.py`：5 项通过，包括真实 FastAPI 业务契约的文件提交—任务完成—修订概览、标准库 multipart 长度、草稿/确认成果分离、404/服务不可达错误、非法或公网配置拒绝。此处 Doclib 是测试替身，不是模型解析。
- `.venv/bin/python -m pytest -q tests/business`：93 项通过、2 个依赖弃用警告。不能把 Mac 测试解释为麒麟、真实模型或完全断网验收。

## 未完成与边界

- P5 不是完整验收：业务 API 暂无业务身份映射下的搜索、任意定位器续读和结构树接口；Skill 不绕道官方 CLI/Doclib。证据“链接”目前是业务证据 ID/locator，不声称浏览器深链已实现。
- 尚未对真实长文档、真实解析结果、同一文档多个修订/成果版本和麒麟离线 Agent 安装进行端到端验证。Agent 包离线导入和运行时系统依赖待现场验证。
- 自有 Skill 能上传业务文档；因为 API 开放且无权限控制，网络可达客户端均可上传或读取。网络策略、数据保留和审计只能由隔离区部署边界及后续现场制度保证。
