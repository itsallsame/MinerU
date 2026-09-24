# 当前 master 跨层自动化回归

日期：2026-09-24。被测源码提交：`19895d7014143e4547c095819af4a80798607202`；测试前工作树干净。环境为 Mac 本地开发机，非目标麒麟 x86_64/NVIDIA 机器。

| 层 | 命令与结果 |
| --- | --- |
| MinerU 上游单测 | `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider -o addopts= tests/unittest`：2832 passed、4 skipped、2 warnings，退出码 0 |
| 业务后端 | 同一 pytest 参数，目标 `tests/business`：351 passed、2 skipped、2 warnings，退出码 0 |
| 业务前端单测/构建 | 在 `business-web` 执行 `pnpm test && pnpm build`：13 项单测通过，8 个离线 Web 制品构建成功，退出码 0 |
| 浏览器回归 | 在 `business-web` 执行 `pnpm test:browser`：24 项离线 Chromium/Playwright 回归通过，退出码 0 |

两个 Python 测试集的警告分别来自第三方 `jieba` 的 `pkg_resources` 弃用和 FastAPI/Starlette TestClient 的 `httpx` 弃用；本次无测试失败。跳过项仍按跳过记录，不视为通过。浏览器回归采用项目脚本启动的离线 Chromium，不代表目标机浏览器或真实模型服务。

这是代码层回归，不是四类真实标注样本的解析准确率，也不是离线麒麟/NVIDIA 的镜像、权重加载、运行态、吞吐或备份恢复验收。后续若合入新源码或更换模型/镜像，需在对应发布快照重新运行；目标机还须在稳定部署窗口归档发布、制品、运行态与真实样本报告链。
