# wheelhouse 发布清单与 Docker 构建上下文一致性（2026-09-24）

## 发现与修复

`.dockerignore` 仅允许 `wheelhouse/requirements.lock` 与 `wheelhouse/` 根目录下的 `*.whl` 进入两个代码镜像的构建上下文。然而 `release_manifest.build_release_record` 先前用递归扫描记录 wheelhouse 内的所有文件。若准备目录遗留说明文件、临时下载文件或子目录中的旧 wheel，发布清单会为它们记哈希，导入校验也能通过，但 Docker 实际没有收到这些文件，清单记录的制品集合与构建输入不一致。

现在发布清单和导入制品核验共用的校验只接受根目录中的 `requirements.lock` 与 `*.whl`；任何其他文件、子目录或符号链接均拒绝发布。准备目录需清理/另建新版本后再生成清单，不会静默忽略额外内容。该约束与现有 `.dockerignore` 白名单对应，模型仍独立于代码镜像。

## 验证

- 新增创建侧负例：`notes.txt`、子目录 `old.whl`、隐藏临时文件。修复前 3 个用例均未抛错，修复后全部拒绝。
- 新增导入侧负例：发布记录生成后出现 `transfer-notes.txt`，导入校验拒绝。
- `tests/business/test_release_manifest.py` 与 `test_verify_offline_release.py` 共 21 项通过；Ruff 通过。
- `test_offline_configuration.py` 在可访问 Docker daemon 的环境中 12 项通过，其中实际 scratch 构建证明嵌套旧 wheel 也未进入上下文。
- 最终完整业务测试为 197 通过、1 跳过；跳过项与本修复无关。Ruff 与 diff 检查通过。

本证据仅证明 Mac 上清单逻辑与本地 Docker 构建上下文一致。真实 linux/amd64 NVIDIA 基础镜像、Linux wheelhouse、麒麟隔离机上的两个代码镜像及模型加载仍未验收，P1/P6 保持未完成。
