# QA-001 上游单测与业务测试基线

日期：2026-09-24。测试源码提交：`63b3e8acc2205dc71b40852defcdeda0098494ec`（测试运行时工作树干净）。环境：macOS 27.0（26A428）、Python 3.13.5、pytest 9.1.1；允许测试在本机临时 Unix/TCP socket 启动 Doclib。未使用麒麟 x86_64/NVIDIA 生产环境。

按功能清单中的原始命令分别执行：

- `.venv/bin/python -m pytest tests/business`：171 passed、1 skipped、2 warnings，退出码 0；跳过项为未显式开启的 Chromium/临时服务集成测试，另已用 `MINERU_RUN_LIVE_BROWSER=1` 单独验证。
- `.venv/bin/python -m pytest tests/unittest`：2798 passed、4 skipped、2 warnings，退出码 0。两个警告均来自 FastAPI/Starlette 测试依赖弃用路径。

这只是当前 fork 的 Mac 自动化基线，不证明 GPU 模型、四类真实业务样本或麒麟隔离部署的正确性。上游 4 个跳过项的逐项原因未由本次原始命令输出，后续升级前应加 `-rs` 收集；不能把跳过项记为通过。该项无生产数据变更，回滚仅需撤销清单与证据记录。
