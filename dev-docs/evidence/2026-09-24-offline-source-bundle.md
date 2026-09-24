# 离线源码增量交付（2026-09-24）

## 已实现

新增 `scripts/offline_source_bundle.py`，把干净 `master` 的确定提交打成带 SHA-256 清单和完成标记的源码包。首次交付为 `source.git/` 浅层裸仓库，只含当前提交及文件树，保留真实提交 ID；后续以目标当前提交为基线生成 `source.bundle`，仅携带后续 Git 对象。`--reuse-wheelhouse` 要求依赖声明输入未变。目标侧验证包文件、哈希、Git 引用和指定旧提交，并只允许干净的 `master` 作为增量导入基线。增量导入使用 `fetch` 加 `merge --ff-only`，不覆盖未提交修改。传输路径、宿主模型、前端构建产物和 wheelhouse 保持独立；操作命令与条件写在 `dev-docs/offline-deployment.md`。

真实 fork 历史核对发现：当前源码树不含模型权重，但历史里有已删除的 `slanet-plus.onnx`（7,758,305 字节）和 `yolo_v11_ft.pt`（3,204,667 字节）。原完整 Git bundle 会把这两份旧权重再次传输，故不能称为“无模型”。本次改为浅层首次快照；生成和验证阶段均拒绝当前树中的权重后缀，增量包还拒绝更新范围内即使后来删除的权重对象。

## Mac 合成验证

- `tests/business/test_offline_source_bundle.py` 6 项通过：浅层首次包克隆与目标复核、从浅层基线增量快进、外部模型文件不变、历史模型对象未进入首包、短暂误提交权重的增量包被拒、依赖输入变化时拒绝复用 wheelhouse，以及脏工作树、重复目标、错误基线、包/清单篡改拒绝。
- Ruff lint 与 format 检查通过。完整 Mac 业务测试：232 通过、1 跳过（2 项第三方依赖警告）。
- 该脚本要求先提交并保持干净 `master`，不能把未提交工作误当作可发布源码。真实 fork 的 `8ef0b9153837cd14939890af47a6d21a3290c693` 已在 Mac 上生成一次临时浅层首包，约 18 MiB、23 个受哈希约束的仓库文件；`manifest.json` SHA-256 为 `3dcb7b354bc1e74f9e9d853e0dd6d10ad6005e01f3d7fca3caed184630709c3a`。命令行包校验、从包克隆、目标侧包校验和 `git fsck` 均通过，目标显示 shallow=true 且 HEAD 与原 fork 提交相同。两份历史模型对象在克隆后执行 `git cat-file -e` 均返回不存在。此包存放在临时目录，仅作为 Mac 验证证据，不是麒麟正式发布制品。

## 信任和验收边界

包内哈希与完成标记只检查一致性，不是签名。隔离区须通过独立可信渠道核对预期 manifest 摘要。依赖输入未变也不证明现有 wheelhouse 的内容、Linux Python ABI、NVIDIA 基础镜像或 vLLM/模型兼容。当前本机仅有 arm64 的 `mineru:4.0.2`，缺 amd64 NVIDIA 基础镜像、真实 Linux wheelhouse、模型目录和四类标注样本；因此 INF-026 及目标验收保持未完成，不能声称麒麟离线部署已通过。
