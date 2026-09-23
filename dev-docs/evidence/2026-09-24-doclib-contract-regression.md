# QA-003 Doclib 契约回归（Mac）

验收对象是当前 fork `master` 的 Doclib 公共接口与业务接入契约，不是麒麟目标机部署验收。源代码基线为 `15259256`。

| 契约面 | 本仓库现有回归证据 |
| --- | --- |
| 真实独立 Doclib 进程与客户端 | `tests/business/test_doclib_contract.py`：Unix socket、显式本地 TCP、单实例占用与重启 |
| 文档身份、缓存与历史读取 | 同文件：SHA-256 身份、同内容复用、指定 parse ID 不串到较新批次、历史 locator、备份恢复 |
| 业务上传到 Doclib | 同文件及 `tests/business/test_doclib_gateway.py`：共享路径、符号链接拒绝、格式/档位、原文身份不符拒绝 |
| 格式与结构 | 真实 HTML、文本 PDF、合成 DOCX/PPTX/XLSX Flash；嵌套块结构和无本地图片模型的明确错误 |
| 上游 Doclib 行为 | `tests/unittest/test_doclib_*`：接口、配置、缓存、定位器、渐进读取、实例锁等 |

2026-09-24 在 Mac/Python 3.13 使用官方 pytest 环境复跑：

- `.venv/bin/python -m pytest tests/business`：**175 passed、1 skipped**，2 条依赖弃用警告。跳过项为需显式开启的真实 Web/浏览器冒烟测试。
- `.venv/bin/python -m pytest tests/unittest --tb=short`：**2798 passed、4 skipped**，2 条依赖弃用警告。`--tb=short` 只改变失败展示，不改变收集或执行范围。
- 上游单测首次在受限环境运行时出现 25 failures、51 errors，失败集中在本机 socket/服务监听权限；在允许本地临时 socket 后，原测试范围全部通过。业务测试本轮直接在允许该权限的环境通过。

判定：QA-003 的 **Mac Doclib 契约回归通过**。这不证明麒麟 x86_64 + NVIDIA、物理断网、容器间 TCP、挂载权限或真实业务文档的解析质量。上述目标环境门槛仍在 P1/P2/P6 中保持未完成；不能用本记录替代现场验收。业务 API 保持开放，不加入登录、用户或权限管理。
