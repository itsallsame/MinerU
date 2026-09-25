# Mac 真链路主流程复验

日期：2026-09-25。源码基线：`5af6dc6f`（本记录的测试启动器修正将作为后续提交）。环境：macOS，项目 `uv` Python 运行真实业务 API 与 Doclib，系统 Python 3.14 运行本机 Chromium/Playwright；临时服务仅监听回环地址，测试结束关闭并删除临时业务库与上传文件。使用仓库公共 PDF `demo/pdfs/demo1.pdf`（SHA-256 `f3b3be345bf2df8979f2491ca9466e078e4fd1d6a216611faa8566e4c44d474b`），并在测试临时目录生成 DOCX/PPTX/XLSX。

执行：`MINERU_BROWSER_PYTHON=/opt/homebrew/bin/python3 MINERU_RUN_LIVE_BROWSER=1 PYTHONPATH=. uv run --no-sync pytest -q -o addopts='' tests/business/test_live_web_smoke.py --tb=short`，结果 **1 passed**。测试不拦截业务 API：Web 上传原生格式与 PDF、Flash 解析、历史内容读取、人工复核、确认不可变成果、下载 JSON，再由自有 Skill 从同一 API 读取逐字段相同的成果；另一次 Skill PDF 上传验证自动字段提取仍是 `machine_unconfirmed`，已确认成果列表仍为空，并验证手动再提取及历史读取。

本轮先发现测试启动器在 `uv run` 下把 `python3` 解析到没有 Playwright 的项目虚拟环境，尚未进入产品流程。改为可配置的 `MINERU_BROWSER_PYTHON` 绝对路径后，Web→API→Doclib→成果→Skill 主链通过；随后发现旧测试仍断言模板 PDF 上传后没有草稿，与当前“解析完成自动排入字段提取”的业务契约冲突。更新断言为“草稿存在且未确认”，复跑通过。两次失败均是测试运行/预期过期，不是产品 API 故障；本次运行也确认最近的成果确认快照参数没有打断真链路。

边界：公共 PDF 与临时 Office 文件没有四类业务人工标注，不验证字段准确率、长文档性能、GPU 模型档位或麒麟离线部署；Chromium 来自当前 Mac，不能替代目标浏览器验收。后续主线应优先完成离线交付的目标机实测与四类真实样本评估；这些输入目前不可用，不能标记 P1/P6 完成。FE-033 人工结构修改范围仍需业务取舍，不能从本冒烟推断已经实现。
