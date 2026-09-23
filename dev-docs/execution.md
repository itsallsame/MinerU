# 业务文档平台执行规范

日期：2026-09-23。分支：`master`。设计依据：[deepdive](deepdive.md)、[架构](2026-09-23-business-platform-design.md)、[计划](2026-09-23-business-platform-plan.md)。任务真值为 [feature-list.json](feature-list.json)。

## 固定前提

- 只在 `itsallsame/MinerU` 的 `master` 开发，官方 `opendatalab/MinerU` 为上游来源；不建新分支。
- 不做旧 3.4/V1 代码、API、JSON 或数据兼容；旧仓库只用于读取业务需求，不整体搬运。
- 生产麒麟 x86_64 + NVIDIA 完全物理隔离；软件依赖、镜像和模型只能通过批准流程离线导入。
- 模型存放宿主持久目录，不进入日常代码镜像；所有运行时网络回退必须禁用。
- 前端与自有 Skill 使用统一业务 API；Doclib 负责文档内容，业务模块负责复核与成果。
- 业务 API 开放使用，不做用户/角色/令牌管理；网络可达范围由物理隔离和部署网络控制。人工确认规则仍保留。
- 仓库已有 `AGENTS.md`/`CLAUDE.md` 编码规范，每次修改先遵守其相对导入、类型、ruff 与副作用约束。

## 开发与验证命令

用户已确认沿用官方 uv、pytest 和 Mac 本地解析冒烟。首次初始化：

```bash
uv venv .venv
uv pip install -e '.[dev,test]'
```

Python 运行从本仓库根目录执行。基础单测：

```bash
.venv/bin/python -m pytest tests/unittest
```

按阶段增加 `tests/business/`，相关测试完成后运行；本地解析冒烟可用 `mineru-kit parse demo/pdfs/demo1.pdf --tier flash --pages all`，但真正推理仍需相应依赖与模型准备。业务前端当前使用零第三方依赖的原生 JS/CSS，`cd business-web && pnpm test && pnpm build` 仅调用已预置 Node，不会安装包；隔离区只导入构建后的 `dist/`。麒麟离线/GPU 实测不以 Mac 结果代替。

Web 关键流程的离线 Chromium 回归在构建后运行 `cd business-web && pnpm test:browser`。准备机须预装 Python Playwright 与 Chromium；脚本只在本机随机端口启动静态资源服务并模拟业务 API 响应，不连接模型，也不代替真实 API、真实样本或目标麒麟验收。另有 `tests/browser_smoke.py` 专用于已启动的真实业务 API，不属于离线套件。

## 会话启动协议

1. 确认工作目录为本 fork，读取 `AGENTS.md`、`git status --short`、`git log -5 --oneline`。
2. 阅读 `dev-docs/claude-progress.txt`、阶段计划和 `feature-list.json` 当前未完成阶段。
3. 只处理一个完整阶段或其明确的原型验证门槛；在进度文件记下开始状态和预计验证。
4. 实施时优先修改计划列出的文件；必要偏离先更新设计和计划，不暗中形成第二套架构。
5. 每项功能有测试后才将 `passes` 设为 `true`；不能把未在目标设备测试的性能指标写成通过。
6. 阶段结束运行格式、单测、契约、端到端等适当验证，检查未提交变更并审查关键风险；记录提交与未完成项，向用户报告后等待下一阶段确认。

## 阶段验证重点

| 阶段 | 不可省略的验证 |
| --- | --- |
| P1 离线底座 | 本地源码入镜像、模型不入构建上下文、无外网启动、模型缺失快速失败、amd64 制品清单 |
| P2 Doclib | 身份、解析、搜索、locator、重启、删除、单实例写入锁、无网络 |
| P3 业务后端 | 证据与模板快照、必核问题阻断、修订记录、持久任务恢复 |
| P4 前端 | 结果状态准确、字段到原文定位、上传档位约束、键盘与窄屏主流程 |
| P5 Skill | 与前端共用状态和证据，不能自动越过人工确认 |
| P6 验收 | 真实标注样本、故障恢复、隔离区构建/启动、麒麟 GPU 性能与回滚 |

## 测试证据与文档

阶段报告写入 `dev-docs/evidence/`，至少记录：源码提交、测试命令、通过/失败计数、样本身份、运行环境、未解决限制和回滚方式。真实样本与个人/业务敏感数据不提交到 Git；只提交脱敏清单及评估脚本。代码审查若无专用 Codex CLI 工具可用，使用本地 `git diff --check`、静态检查、测试与逐文件人工审查，并明确记录替代方式，不能谎称已运行不可用工具。

## 删除规则

旧 3.4/V1 系统的代码和数据不迁移、不兼容。但“允许删除旧实现”不意味着可以递归删除整个父工作区或未确认的用户资料。实际删除前先列出精确目标，检查 Git 追踪/未追踪和数据归属；优先在新 fork 完成可用闭环后清理已确认废弃的旧代码。生产数据只按经批准的保留策略处理。
