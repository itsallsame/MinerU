# P2 Doclib 公开契约原型记录

日期：2026-09-23。环境：Mac、本 fork 的 MinerU 4.0.6、隔离的临时 `MINERU_HOME` 和 Unix socket、`MINERU_MODEL_SOURCE=local`、空模型目录；**非麒麟/NVIDIA、非生产数据**。P1 目标硬件验收仍未完成，本原型按开发计划中的并行例外推进。

## 已执行

- 每例启动独立 `python -m mineru.doclib.app`，用公开 `DoclibClient` 连接；退出时终止进程并清理临时目录。没有修改用户现有 Doclib 数据。
- `DoclibGateway` 使用公开 `DoclibInterface.ensure_parse`，只接收共享目录内的普通文件，禁止文件符号链接、目录外路径、原生格式非 Flash 档位和远程解析；提交前后校验 SHA-256 与 Doclib 返回值。
- 实际生成 HTML、可提取文本 PDF、DOCX、PPTX、XLSX 和 PNG。HTML/PDF/Office 完成 Flash 解析、按内容读取；HTML/PDF 的 locator 续读、HTML 搜索、重复内容/同名不同内容身份、强制重解析、图片入库与缺模型失败、`forget_path` 和服务重启均实测。

验证命令：`.venv/bin/python -m pytest -q -o addopts='' tests/business/test_doclib_contract.py tests/business/test_doclib_gateway.py`，首轮 **13 passed**；原始契约用例 **9 passed**。补充 TCP、互斥和停机恢复 3 项后，`.venv/bin/python -m pytest tests/business/test_doclib_contract.py -q` **12 passed**，随后 `.venv/bin/python -m pytest tests/business -q` **25 passed**（本地 Unix socket/loopback TCP 需授权）。`ruff check` 与 `ruff format --check` 对新增业务文件通过；相关上游架构/接口/单实例锁测试 **48 passed**。默认沙箱不允许 `AF_UNIX.bind`，该权限错误不代表 Doclib 契约失败。

## 已确认的系统语义

1. `ensure_parse(ParseRequest.path)` 读取的是 **Doclib worker 可访问的绝对路径**，且可直接入库，不要求先 `create_scan`。业务 API 与 worker 分容器时必须共享上传目录，worker 挂只读；只靠 API 容器私有路径不可行。
2. 同字节不同路径得到相同 SHA-256/短 ID；同名不同内容得到不同 SHA-256。业务文档 ID 不能直接等同文件路径，也不能只用内容哈希表示一个业务任务。
3. HTML/PDF/Office 的 Flash 解析可在这组简单合成样本上完成；PDF 单行极短文本在空模型目录下进入 OCR 路径并失败，多行明确文本则无需权重完成。**Flash 并不保证完全无模型**。
4. 图片在空模型目录下仍可入库获得文档身份，但解析任务以 `parse_failed` 和模型未就绪信息结束；不能把“入库成功”当“解析成功”。
5. 强制重解析产生新的 `ParseInfo.id`。公开 locator 格式是 doc/tier/page/block/char，不带 parse ID；因此只能作为导航提示。业务证据必须冻结解析 ID、原文片段和可用的页/块/bbox，确认结果不得随重解析静默变更。
6. 服务重启后可按内容哈希和原 locator 读取简单 HTML 样本。`forget_path(dry_run=False)` 会移除活跃文件行，但若源文件仍在，`get_doc_by_path` 会自动重新入库；删除业务记录必须另订策略，不能直接委托 `forget_path`。
7. 显式 `DoclibClient(base_url=...)` 已通过 Mac 本机独立进程的 loopback TCP 测试，且可以经业务适配层提交共享路径文件；**容器间 TCP 通信、无鉴权内部接口的隔离边界尚未实测**。生产网络不得将 Doclib 端口公开给非业务 API 调用方。
8. 第二个 Doclib 进程使用同一 `MINERU_HOME` 时非零退出，原进程仍可服务，证明本机独占锁有效；这不证明跨主机文件锁语义。
9. 在正常停机后复制整个 `MINERU_HOME`，恢复到另一个路径并重启，可按 SHA 和旧 locator 读回简单 HTML 样本。在线热备、跨版本恢复、模型权重与业务库的一致性快照仍未验证；备份流程必须在业务文档/证据库加入后重新设计。
10. 业务上传存储先写入不可解析的临时后缀，限额与 SHA-256 在流式写入中计算，文件同步后用硬链接原子发布为随机文件名；失败/超限/断流不会留下部分源文件，名字碰撞不会覆盖或删除旧文件。已实测发布后的 HTML 经 `DoclibGateway` 完成 Flash 解析。当前是同一 Mac 文件系统上的测试，跨容器挂载路径/权限仍待验证。

## 尚未证明

- PDF Basic/Standard/Advanced 的 GPU 模型执行、图片 OCR 质量、复杂扫描件和真实业务样本；空模型目录不具备该能力。
- Office 原文预览几何信息与 bbox；本次只检验文字内容，不能推断可直接高亮原格式页面。
- 大文档多页 locator 稳定性、解析修订旧内容可复查性、在线/跨版本备份恢复、并发写入与容器间访问控制。
- 麒麟 Linux x86_64 + NVIDIA、物理断网、共享卷权限和重启恢复。

下一步：业务文档 ID/证据快照持久化、上传目录权限和孤儿文件回收；随后接入业务 API。目标硬件到位后回补跨容器 TCP、模型和真实样本验收。

补充验证（同日）：`.venv/bin/python -m pytest tests/business/test_uploads.py -q` **5 passed**；名字碰撞修正后 `.venv/bin/python -m pytest tests/business -q` **31 passed**。上传大小上限当前由调用方传入，尚未固化生产策略；业务事务、孤儿文件回收及访问控制仍未实现。
