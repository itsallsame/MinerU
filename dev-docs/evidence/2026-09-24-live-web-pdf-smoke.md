# 公共 PDF 的真实本机 Web/API/Doclib 联通

日期：2026-09-24。分支：`master`。环境：macOS，回环地址上的临时业务 API 与 Doclib TCP、Chromium；设置 Hugging Face/Transformers/Datasets 离线环境变量、禁用远程 VLM 与 LLM 增强。无 GPU、无模型权重、无 Docker/麒麟目标机。

样本：仓库公共 `demo/pdfs/demo1.pdf`，SHA-256 为 `f3b3be345bf2df8979f2491ca9466e078e4fd1d6a216611faa8566e4c44d474b`；不是四类已标注业务验收样本。

命令：先 `cd business-web && pnpm build`，然后从仓库根目录运行 `MINERU_RUN_LIVE_BROWSER=1 .venv/bin/python -m pytest -q tests/business/test_live_web_smoke.py`。浏览器脚本不拦截或模拟任何业务 API 路由。结果：1 项通过；页面从 0 份文档上传公共 PDF，Flash 解析任务完成，展示 1–13 页解析修订，历史第一页读取到 `afforestation`。测试使用临时目录并在 finally 中终止、等待两个服务进程；不将上传文档或数据库写入仓库。

首次运行在 `get_by_text` 上遇到下拉选项与修订卡片重复命中；限定到修订卡片后原样重跑通过。这是测试选择器问题，不是解析失败。Ruff/格式检查及 `git diff --check` 通过。

边界：这里只覆盖文本型 PDF 的 Flash 本机联通，不证明 OCR、Basic/Standard/Advanced、模型权重、GPU、麒麟部署或四类真实样本准确率。相关 P6 项仍未完成。回滚仅删除 opt-in 测试与文档，不涉及业务数据。
