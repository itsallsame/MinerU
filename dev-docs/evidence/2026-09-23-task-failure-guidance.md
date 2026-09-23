# 解析任务失败原因与恢复入口

日期：2026-09-23。范围：P4 FE-014 失败原因展示、FE-015 任务重试入口的共同业务契约；两个特性分别在特性清单中验收。

后端任务只持久化稳定的 `error_code`，不返回可直接向业务用户展示的底层异常文本。Web 对 `source_integrity_failed`、`doclib_submission_failed`、`doclib_parse_failed`、`parse_coverage_incomplete`、`parse_batch_invalid` 提供中文解释并保留原始代码；未知代码给出保守通用说明。原件完整性校验失败不能靠重试恢复，因此不显示重试按钮，而引导重新上传并核查存储；其余已知失败可重新提交。`uploaded`（中断后待提交）也提供“提交待处理任务”。Web/Skill 仍共用无登录的业务 API；这不是权限规则。

验证：`cd business-web && pnpm test && pnpm build` 为 11 个单测通过、8 个离线资源；`taskFailure` 单测覆盖已知代码、未知代码与完整性失败的不可重试判断。Playwright `upload_retry_race_browser.py` 验证失败说明、完整性失败无重试、可恢复失败重试、待提交任务恢复以及旧重试错误不污染新文档。后端 `.venv/bin/python -m pytest tests/business/test_document_workflow.py -q` 10 个测试通过，覆盖错误代码的产生、重试和源文件完整性拒绝。`git diff --check` 与浏览器测试 Ruff 检查通过。

边界：错误说明来自稳定代码映射，不保证自动诊断底层故障根因；重复失败仍需在隔离区检查服务/模型日志。模拟 API 浏览器测试及 Mac 后端单测不代替真实样本和麒麟 NVIDIA 验收。
