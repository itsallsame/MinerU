# Mac 本地真实启动与业务流程联通

日期：2026-10-02。源码基线：`e2d679a9`（本次仅扩展联通测试与证据）。环境：Mac、Python 3.13 项目虚拟环境、系统 Python Playwright/Chromium；无 GPU、无联网模型下载。样本：仓库公开 `demo/pdfs/demo1.pdf`，SHA-256 `f3b3be345bf2df8979f2491ca9466e078e4fd1d6a216611faa8566e4c44d474b`，另加测试临时生成的 DOCX/PPTX/XLSX。

## 实际运行

先运行 `cd business-web && npm run build`，得到 8 个离线 Web 资源。随后在仓库根目录运行：

```bash
MINERU_RUN_LIVE_BROWSER=1 MINERU_BROWSER_PYTHON=/opt/homebrew/bin/python3 \
  .venv/bin/python -m pytest -q tests/business/test_live_web_smoke.py --tb=short
```

该测试不是模拟 API：在 `/private/tmp` 的独立临时目录内启动真实 Doclib TCP、业务 API 和同源 Web，使用本机随机回环端口；结束时停止两个服务进程并清理临时数据。设置本地模型来源和 Hugging Face/Transformers 离线变量；本次 Flash/原生格式链路不使用 GPU 权重。

第一次运行现有闭环：1 passed in 33.33s。扩展结构编辑后运行：1 passed in 24.52s；最终源码再次复跑：1 passed in 24.82s。实际覆盖：

1. Web 上传公开 PDF → 业务任务完成 → 历史修订第一页读取，取得真实解析文本。
2. Web 读取该 PDF 的原生派生结构 v0 → 选择一个真实非正文节点 → 提交 `set_type`、原因与预期版本 → 读取人工结构 v1；原生 `/structure` 响应前后完全相等。此处仅验证可写可读，不评价“改成正文”是否是正确业务修正。
3. Web 上传临时 DOCX/PPTX/XLSX，分别读到原生解析内容；DOCX 的机器候选必须人工接受后才能形成确认成果，Web 下载 JSON，刷新页面仍能读到结果。
4. 自有 Skill CLI 从同一真实业务 API 读取 Web 已确认成果，以及 PDF 的 `human_edited_structure` v1 和本次编辑的节点 ID；另外以独立 PDF 上传走任务、历史读取、提取和“候选未确认/成果为空”边界。

## 启动形态与边界

本次证明的是**当前 fork 源码在 Mac 本机临时启动后的真实 Web/API/Doclib/Skill 流程**，不是 Docker Compose、常驻服务、GPU 档位、OCR 质量或麒麟生产机器验收。测试数据由临时目录清理，不是可供后续人工查看的持久环境。

检查本机 Docker 发现，当前映射 8080 的容器属于 `/Users/ahaha/Documents/longge/document-intelligence/docker/compose.production.yaml` 的 `document-intelligence` 项目，使用旧 `mineru:4.0.2`；它不是本 fork 的新业务服务。本次测试没有修改或重启它。当前 fork 的 `docker/compose.business.yaml` 是离线麒麟/NVIDIA 部署编排，依赖独立的业务/Worker 镜像、模型目录和清单，不能把旧 8080 页面当作本次闭环证据。

尚未验证：结构编辑的人工质量、四类标注样本、模型档位推理和隔离麒麟/NVIDIA 现场部署。这些不影响本次“Mac 本地启动、业务流程可走通”的结论。
