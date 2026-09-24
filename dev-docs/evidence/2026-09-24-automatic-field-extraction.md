# 解析完成后自动排入字段提取

日期：2026-09-24。BE-031。开放系统无用户、角色或权限管理；自动化只生成可复核的机器候选，不提交人工决定或确认成果。

此前解析完成只保存修订，上传时已选模板的文档仍需 Web/Skill 再发起一次提取。现在 `complete_task_with_revision` 在同一个业务 SQLite 写事务内完成任务、创建/复用修订并排入首次提取。只针对上传时已绑定且冻结模板版本的文档；未选模板不猜测类型。重复刷新、并发完成或相同修订复用不重复生成自动运行；已有失败/完成运行也不被自动重排，人工 `POST /revisions/{id}/extractions` 仍可显式再生成。若提取运行插入失败，修订和任务完成一起回滚。

Mac 定向测试验证有/无模板、幂等、并发、取消前无运行、SQLite 写入失败原子回滚，以及自有 Skill 读取最新修订时看到 `machine_unconfirmed` 而非已确认成果。真实 Doclib Unix-socket 联调从 HTML 上传、解析完成、自动入队到提取线程产出带原文依据的标题候选，无需手动 POST。Web 无运行时的说明改为自动提取语义；离线 Chromium 合成回归和原有手动重生成入口仍通过。自有 Skill 的工作流说明改为先读自动运行、明确请求重生成时才发写入命令，`quick_validate.py` 检查通过。

完整业务测试 `.venv/bin/python -m pytest -q tests/business`：279 passed / 1 skipped。Web `pnpm test`：12 passed；`pnpm build`：8 个离线资产；`python3 business-web/scripts/run_browser_tests.py`：23 个离线 Chromium 回归通过。最初误用项目 `.venv` 运行浏览器回归，因该环境未安装 Playwright 而在导入阶段失败；改用本机已安装 Playwright 的 `python3` 后通过。Ruff/JSON/diff 检查见本轮进度记录。

限制：合成与本地 HTML 联调不证明公文、论文、研报、报纸四类真实样本的字段质量，也不证明麒麟/NVIDIA 容器、模型或离线回退；最终目标验收独立保留。
