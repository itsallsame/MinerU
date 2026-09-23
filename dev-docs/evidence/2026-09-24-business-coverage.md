# QA-002 业务模块测试覆盖率基线

日期：2026-09-24。测量源码提交：`63b3e8acc2205dc71b40852defcdeda0098494ec`，后续仅有测试基线文档提交；环境为 macOS 27.0、Python 3.13.5、pytest 9.1.1/pytest-cov 7.1.0。完整 `tests/business` 含单元、契约和部分集成测试，不能把覆盖率解释为“仅单测”。

测量命令：`.venv/bin/python -m pytest -q -o addopts= --cov=mineru.business --cov-branch --cov-report=term-missing --cov-report=json:/临时目录/business-coverage.json tests/business`。用 `-o addopts=` 排除项目默认的全 MinerU HTML 覆盖设置，仅测 `mineru.business`；启用分支覆盖。结果：171 passed、1 skipped（opt-in 的真实 Chromium 联通测试另行执行）、2 个测试依赖弃用警告，退出码 0。业务代码 2297 条可测语句中 2137 条覆盖，522 条分支中 418 条覆盖；语句覆盖率 93.03%，分支覆盖率 80.08%，综合覆盖率 90.63%（终端显示四舍五入为 91%）。

低覆盖优先项：`services/extraction_worker.py` 80.39%、`domain/templates.py` 84.91%、`services/extractions.py` 85.00%；`api/server.py` 87.84%、`documents/uploads.py` 88.28%。这些数字指向进程停止/重启、异常输入与失败回滚等未触达路径，下一轮应按风险补测，而非仅追求数字。

按功能清单的另外两条原命令 `.venv/bin/python -m pytest tests/business` 与 `.venv/bin/python -m pytest tests/unittest` 同日通过，分别 171 passed/1 skipped 与 2798 passed/4 skipped；见 [QA-001 基线](2026-09-24-test-baselines.md)。本记录没有设置或宣称覆盖率达标阈值，也不证明真实样本准确率、GPU 推理或麒麟物理隔离。回滚只撤销基线记录，不影响代码或数据。
