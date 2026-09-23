# P2 Doclib 公开契约原型记录

日期：2026-09-23。环境：Mac、本 fork 的 MinerU 4.0.6、隔离的临时 `MINERU_HOME` 和 Unix socket、`MINERU_MODEL_SOURCE=local`、空模型目录；**非麒麟/NVIDIA、非生产数据**。P1 目标硬件验收仍未完成，本原型按开发计划中的并行例外推进。

## 已执行

- 每例启动独立 `python -m mineru.doclib.app`，用公开 `DoclibClient` 连接；退出时终止进程并清理临时目录。没有修改用户现有 Doclib 数据。
- `DoclibGateway` 使用公开 `DoclibInterface.ensure_parse`，只接收共享目录内的普通文件，禁止文件符号链接、目录外路径、原生格式非 Flash 档位和远程解析；提交前后校验 SHA-256 与 Doclib 返回值。
- 实际生成 HTML、可提取文本 PDF、DOCX、PPTX、XLSX 和 PNG。HTML/PDF/Office 完成 Flash 解析、按内容读取；HTML/PDF 的 locator 续读、HTML 搜索、重复内容/同名不同内容身份、强制重解析、图片入库与缺模型失败、`forget_path` 和服务重启均实测。

验证命令：`.venv/bin/python -m pytest -q -o addopts='' tests/business/test_doclib_contract.py tests/business/test_doclib_gateway.py`，**13 passed**；按任务清单的原始命令 `.venv/bin/python -m pytest tests/business/test_doclib_contract.py` 单独复跑 **9 passed**（本地 Unix socket 需授权）。`ruff check` 与 `ruff format --check` 对新增业务文件通过；相关上游架构/接口/单实例锁测试 **48 passed**。默认沙箱不允许 `AF_UNIX.bind`，该权限错误不代表 Doclib 契约失败。

## 已确认的系统语义

1. `ensure_parse(ParseRequest.path)` 读取的是 **Doclib worker 可访问的绝对路径**，且可直接入库，不要求先 `create_scan`。业务 API 与 worker 分容器时必须共享上传目录，worker 挂只读；只靠 API 容器私有路径不可行。
2. 同字节不同路径得到相同 SHA-256/短 ID；同名不同内容得到不同 SHA-256。业务文档 ID 不能直接等同文件路径，也不能只用内容哈希表示一个业务任务。
3. HTML/PDF/Office 的 Flash 解析可在这组简单合成样本上完成；PDF 单行极短文本在空模型目录下进入 OCR 路径并失败，多行明确文本则无需权重完成。**Flash 并不保证完全无模型**。
4. 图片在空模型目录下仍可入库获得文档身份，但解析任务以 `parse_failed` 和模型未就绪信息结束；不能把“入库成功”当“解析成功”。
5. 强制重解析产生新的 `ParseInfo.id`。公开 locator 格式是 doc/tier/page/block/char，不带 parse ID；因此只能作为导航提示。业务证据必须冻结解析 ID、原文片段和可用的页/块/bbox，确认结果不得随重解析静默变更。
6. 服务重启后可按内容哈希和原 locator 读取简单 HTML 样本。`forget_path(dry_run=False)` 会移除活跃文件行，但若源文件仍在，`get_doc_by_path` 会自动重新入库；删除业务记录必须另订策略，不能直接委托 `forget_path`。
7. Doclib 客户端支持显式 `base_url`，但**容器间 TCP 通信、无鉴权内部接口的隔离边界尚未实测**；当前实测仅 Unix socket。

## 尚未证明

- PDF Basic/Standard/Advanced 的 GPU 模型执行、图片 OCR 质量、复杂扫描件和真实业务样本；空模型目录不具备该能力。
- Office 原文预览几何信息与 bbox；本次只检验文字内容，不能推断可直接高亮原格式页面。
- 大文档多页 locator 稳定性、解析修订旧内容可复查性、数据备份恢复、并发写入与容器间访问控制。
- 麒麟 Linux x86_64 + NVIDIA、物理断网、共享卷权限和重启恢复。

下一步：业务文档 ID/证据快照持久化契约、共享上传目录的原子写入与权限；随后接入业务 API。目标硬件到位后回补 TCP/容器、模型和真实样本验收。
