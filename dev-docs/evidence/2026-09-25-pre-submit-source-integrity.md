# 提交前原件完整性闸门（2026-09-25）

## 问题与边界

业务上传时已保存原件 SHA-256，但重试路径原先只在 Doclib `ensure_parse` 返回之后才比较注册哈希。若宿主机上的原件被改写，同时 Doclib 不可用，任务会被当作可重试的 Doclib 提交失败；恢复连接后还可能把改写后的原件送入 Doclib。只读文件权限和容器挂载用于降低误操作，不能抵御同 UID 或宿主机管理员篡改。

## 修正

`DocumentWorkflow` 把业务文档注册的 SHA-256 传给 `DoclibGateway.submit`。网关复用原有的提交前文件哈希计算，在调用 `ensure_parse` 前核对注册值；不匹配时抛出 `DocumentIntegrityError`，由业务任务记录为终止的 `source_integrity_failed`，不向 Doclib 发送该文件。既有提交后文件哈希、Doclib 响应身份检查仍保留。未增加一次额外的文件读取，也未变更数据库 schema、模型或鉴权设计。

## 验证

- 先添加网关和工作流负例，复现网关不接受 `expected_sha256`、Doclib 不可用时重试未抛出完整性错误。
- 修正后 `test_doclib_gateway.py` 与 `test_document_workflow.py`：37 passed。
- Mac 本地完整业务测试：363 passed、2 skipped、2 dependency warnings（`pytest -q -p no:cacheprovider -o addopts= tests/business`）。Ruff 对四个变更代码/测试文件检查通过。
- 两个负例均断言改写的原件在调用 Doclib 前被拒绝；工作流负例还核对持久任务错误码为 `source_integrity_failed`。

这只证明当前 Mac 本地契约；四类脱敏真实文档与标注、物理隔离麒麟 x86_64＋NVIDIA 目标机入口目前都不可用。未进行真实效果、GPU、断网部署或目标机故障验收，相关功能门槛继续保持未通过。
