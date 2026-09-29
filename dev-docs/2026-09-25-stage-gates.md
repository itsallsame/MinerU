# MinerU 4.0 业务平台阶段门禁现状

2026-09-27 补充：用户已明确目标麒麟＋NVIDIA 的**实际部署由另一团队完成**，开发侧负责可交付文档/工具与接收现场证据；见[部署交接单](2026-09-27-deployment-handoff.md)。工作区已有若干 PDF/图片可作冒烟候选，见[样本盘点](2026-09-27-existing-samples.md)，但尚无四类带人工标注的业务验收集。因此下表是 2026-09-25 基线，不把交接文档当作现场通过。

2026-09-29 补充：`~/Downloads` 三份公开论文/白皮书/报纸 PDF 的 Mac 解析及真实业务 API 联通见[测试记录](evidence/2026-09-29-downloads-sample-smoke.md)；它们增加了真实文件覆盖，但不改变四类人工标注或目标硬件门禁。同日用户已选 FE-033 方案 B，详细契约见[结构编辑范围](2026-09-24-structure-edit-scope.md)。

核对日期：2026-09-25；fork `itsallsame/MinerU` `master` 提交 `9206a9dc`。本表把“代码已存在”“Mac 已验证”和“最终验收已完成”分开。`feature-list.json` 的 `passes` 是逐项最终声明，尤其 P1/P2/P3 的多数条目包含现场门槛；不能把 `false` 直接解读为代码尚未开发，也不能把 Mac 测试通过解读为麒麟上线就绪。本表不修改各项 `passes`。核对时复跑 `tests/business/test_doclib_contract.py` 与 `test_review_results.py`，当前 Mac 为 48 passed（2 条依赖警告）；这只是这两个文件的定向结果，不替代完整业务套件。

| 阶段 | 当前可核验的交付物与证据 | 尚未完成的门禁 | 下一步 |
| --- | --- | --- | --- |
| P0 架构/计划 | [架构](2026-09-23-business-platform-design.md)、[阶段计划](2026-09-23-business-platform-plan.md)、本执行规范和 feature 清单已在 fork；不迁移 3.4/V1，不设用户/权限，Web 与自有 Skill 走同一业务 API。 | 随真实环境、业务反馈更新，不把初版设计当最终部署事实。 | 以本表检查新任务是否推进主流程或阶段门禁。 |
| P1 离线交付 | `docker/worker/Dockerfile`、`docker/business-api/Dockerfile`、`docker/compose.business.yaml`、模型/镜像/发布核验工具与[离线手册](offline-deployment.md)存在；Mac 上实际 Web/API/存储代码增量包已验包并导入旧版检出，见[代码增量证据](evidence/2026-09-25-current-code-only-offline-transfer.md)；另有绑定源码提交的[独立 Web 静态包](evidence/2026-09-25-offline-web-bundle.md)。模型作为宿主只读目录，不在源码/Web 包中。 | 无麒麟 x86_64＋NVIDIA 主机、目标基础镜像、Linux wheelhouse、真实权重或可用本机 Docker daemon；未执行目标构建、断网启动、挂载、GPU 模型加载、备份回退。`INF-020/026/027` 等保持未完成。 | 获得目标环境后按手册第 1–9 步生成并核对发布/制品/运行态报告；不得以 Compose 静态文本或 Mac 模拟测试代替。 |
| P2 Doclib | `mineru/business/documents/` 与 `tests/business/test_doclib_contract.py` 包含公开客户端、上传身份、PDF/Office Flash、历史修订/定位器与重启等 Mac 真服务验证；[最新真链路](evidence/2026-09-25-live-main-flow-recheck.md)再次联通。 | 图片/OCR 与四档本地模型的目标 GPU 行为、容器间 Doclib 通信及真实复杂样本仍无现场证据；P2 feature 的 `false` 不应被批量翻转。 | 在 P1 目标环境运行相同契约与格式矩阵，逐项记录 PDF/图片/Office、定位精度和失败语义。 |
| P3 业务后端 | `mineru/business/api/`、`store/`、领域与工作流代码存在；自动提取保持未确认、人工复核/冻结证据/不可变成果与开放 API 已有 Mac 业务测试。最近完整业务套件 401 passed、2 skipped；真链路已验证 Web 确认后 Skill 读取相同成果。 | 本地测试不证明目标重启、磁盘故障、并发容量和真实四类字段质量；schema 18 没有旧库兼容或迁移。P3 `passes=false` 的现场门槛尚在。 | 目标机上跑状态恢复与业务 API 契约，四类样本到位后核验字段/证据质量；上线前固定数据库备份/回退策略。 |
| P4 业务 Web | `business-web/` 的上传、文档库、解析阅读、证据/复核、成果、模板与运营页面已存在；Mac Web 单测 13 通过、离线 Chromium 29 项通过；[真链路](evidence/2026-09-25-live-main-flow-recheck.md)覆盖上传到成果导出。当前清单 47/50。 | `FE-033` 已定方案 B 但尚无版本化派生树与编辑 UI；`FE-046/047` 的完整键盘/屏幕阅读器、真实网络和目标浏览器验收未完成。 | 按 B 契约先补 Doclib 完整文字、业务版本/操作，再做 Web/Skill 同源入口与真实样本复核；其余两项做阶段级现场可用性验收。 |
| P5 自有 Skill | `skills/business-documents/` 22/22 Mac 阶段条目已标通过；[阶段审查](evidence/2026-09-24-p5-skill-phase-review.md)和[最新真链路](evidence/2026-09-25-live-main-flow-recheck.md)证明与 Web 使用同一业务 API、机器候选不自动确认、成果对象一致。 | 目标麒麟与四类样本上的 Agent 实际工作流、隔离网络和性能未验收。 | 随目标发布重跑 Skill→业务 API→Doclib→成果流程，检查不绕过人工确认。 |
| P6 系统验收 | QA-001–006 的自动化基线已记录；[四类样本协议与只读评估工具](real-sample-acceptance.md)存在。 | QA-007–035 中 29 项未完成：四类真实脱敏原文/标注、模型与 GPU、隔离断网、故障恢复、备份回退和最终报告。用户目前无法提供样本或目标机入口；不能填准确率、吞吐或“生产通过”。 | 输入具备时先锁样本、指标阈值和发布清单，再跑工具及人工复核，最后出逐项通过/失败报告。 |

## 执行排序与停止线

1. 在没有目标机/真实样本时，Mac 工作限于能直接降低主流程或交付风险的代码、契约、可重现验证；不要把已具备的 29 项合成浏览器回归继续扩展成无穷的兜底清单。
2. `FE-033` 已选择方案 B；不得将只读预览当完整文本保存，也不得将派生结构当成原生结构或冻结证据。完整版本/操作契约、Web 与 Skill 同源验证后方可标通过。
3. P1/P6 的真实麒麟/NVIDIA/物理断网与四类样本门禁不可由 Mac 证据替代；目标输入到位前，Goal 保持进行中，不声称系统可上线。
