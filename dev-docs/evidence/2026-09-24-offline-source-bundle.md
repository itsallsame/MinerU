# 离线源码增量交付（2026-09-24）

## 已实现

新增 `scripts/offline_source_bundle.py`，把干净 `master` 的确定提交打成带 SHA-256 清单和完成标记的 Git bundle。可选择以目标当前提交为基线，仅携带后续 Git 对象；`--reuse-wheelhouse` 要求依赖声明输入未变。目标侧验证包的文件集合、哈希、Git 引用和指定旧提交，并只允许干净的 `master` 作为增量导入基线。导入使用 `fetch` 加 `merge --ff-only`，不覆盖未提交修改。传输路径、宿主模型、前端构建产物和 wheelhouse 保持独立；操作命令与条件写在 `dev-docs/offline-deployment.md`。

## Mac 合成验证

- `tests/business/test_offline_source_bundle.py` 4 项通过：首次包克隆与目标复核、从指定旧提交增量快进、外部模型文件不变、依赖输入变化时拒绝复用 wheelhouse，以及脏工作树、重复目标、错误基线、包/清单篡改拒绝。
- Ruff lint 与 format 检查通过。完整 Mac 业务测试：230 通过、1 跳过（两项依赖弃用警告）。
- 未在本机生成真实 fork 的发布包；该脚本要求先提交并保持干净 `master`，不能把当前未提交工作误当作可发布源码。

## 信任和验收边界

包内哈希与完成标记只检查一致性，不是签名。隔离区须通过独立可信渠道核对预期摘要。依赖输入未变也不证明现有 wheelhouse 的内容、Linux Python ABI、NVIDIA 基础镜像或 vLLM/模型兼容。当前本机仅有 arm64 的 `mineru:4.0.2`，缺 amd64 NVIDIA 基础镜像、真实 Linux wheelhouse、模型目录和四类标注样本；因此 INF-026 及目标验收保持未完成，不能声称麒麟离线部署已通过。
