# 离线 wheelhouse 新目录发布（2026-09-24）

## 问题与改动

联网准备脚本原先直接 `mkdir -p wheelhouse`，然后覆盖 `requirements.lock` 并下载 wheel。复用旧目录会留下上版 wheel；若编译或下载中途失败，也会留下看似可供构建的半成品。即便后续 Docker 的 `--require-hashes` 能约束实际安装，混杂目录仍增加传输量并混淆发布/回退边界。

脚本现在拒绝已有目录或符号链接，先在同一工作区的 `.wheelhouse-stage.*` 目录生成锁和 wheel，两步成功才发布为 `wheelhouse/`。失败不删除暂存文件，供人工检查；旧 wheelhouse 不被覆盖或清理。发布清单在上一轮已限定根目录只能包含锁和 wheel，形成准备与发布双层检查。

## 验证与限制

- `sh -n scripts/prepare-worker-wheelhouse.sh` 通过。
- 新增 `tests/business/test_prepare_worker_wheelhouse.py`：模拟 Linux x86_64 命令，验证已有旧 wheel 拒绝且内容不变、锁编译失败、wheel 下载失败、成功发布；4 项通过。Ruff 通过。
- 完整 Mac 业务测试 201 通过、1 跳过；该跳过项不涉及 wheelhouse 准备流程。
- 这些测试没有从 PyPI 下载真实依赖，也没有运行 linux/amd64 基础镜像，因此不证明 Python ABI、CUDA、vLLM 或麒麟目标机兼容性。INF-019 仍不标完成。
