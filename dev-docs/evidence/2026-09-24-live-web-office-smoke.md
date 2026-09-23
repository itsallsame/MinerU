# DOCX/PPTX/XLSX 的真实本机 Web/API/Doclib 联通

日期：2026-09-24。分支：`master`。环境：macOS、回环地址上的临时业务 API 与 Doclib TCP、Chromium，使用 Flash 原生解析；不使用模型权重或 NVIDIA。测试通过 `MINERU_RUN_LIVE_BROWSER=1 .venv/bin/python -m pytest -q tests/business/test_live_web_smoke.py` 运行，结束时关闭服务并清理临时目录。

在测试的临时目录内用 `python-docx`、`python-pptx`、`openpyxl` 分别生成包含唯一标记的 DOCX、PPTX、XLSX。Chromium 不拦截任何业务 API：在同一页面批量上传三份文件，确认各自解析任务完成、解析修订为 Flash、历史页中可读到对应标记，并确认 Office 原件仅提示下载、没有伪造预览能力。公共 PDF 流程仍在同一测试中重跑。结果：1 项多格式真实链路测试通过；Ruff/格式检查与 diff 检查通过。

这些 Office 文件是合成夹具，只证明原生格式接入及业务 Web/API/Doclib 通路，不证明真实 DOCX/PPTX/XLSX 文档的版式质量、字段准确率、证据命中、GPU 档位或麒麟离线部署。P6 对应真实样本和目标机项目继续保持未完成。回滚只涉及测试和文档，不影响业务数据。
