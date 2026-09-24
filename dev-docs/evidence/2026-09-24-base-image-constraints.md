# 离线 wheelhouse 基础镜像版本核对（2026-09-24）

## 缺口与处理

原准备流程允许人工填写 `MINERU_BASE_CONSTRAINTS`，只用正则检查存在 `torch==`、`torchvision==`、`vllm==`；不能证明这些版本来自将要使用的基础镜像。错误约束可能在联网准备时下载一套与目标 Python/CUDA/vLLM 不一致的依赖，直到隔离区构建或运行才暴露。

新增 `scripts/inspect_worker_base.py`：以已导入的本地镜像引用和独立记录的不可变镜像 ID 为输入，检查 `docker image inspect` 的 `linux/amd64` 与精确 ID；随后仅用 `docker run --pull=never --network=none --read-only --entrypoint python3 <不可变镜像ID>` 读取该镜像的 Python 主次版本及已安装 Torch、Torchvision、vLLM 版本，避免标签在检查与运行之间改指。宿主准备机 Python ABI 必须与镜像相同。`--output` 仅新建三项精确约束，拒绝覆盖；`--verify` 拒绝被修改或不匹配的约束文件。`scripts/prepare-worker-wheelhouse.sh` 在创建暂存目录、编译锁或下载 wheel 前强制执行同一核对；uv 锁解析显式使用被核对的同一个 `python3`。完整操作顺序见 `dev-docs/offline-deployment.md`。

## 验证与边界

- Mac 模拟 Docker 回归覆盖正确版本、不联网/不拉取标志、arm64 错误、镜像 ID 错误、Python ABI 错误、缺少或格式错误的包版本、约束输出不可覆盖与约束漂移拒绝；准备脚本测试覆盖基础镜像核对失败时不创建 wheelhouse 暂存目录。
- `sh -n scripts/prepare-worker-wheelhouse.sh`、Ruff、针对性 12 项测试通过；完整 Mac 业务测试 240 通过、1 跳过。最后将容器探测从可变标签改为不可变 ID 后，针对性回归重新通过。
- 另用本机现成的 arm64 镜像按不可变 ID 执行一次 `docker run --rm --pull=never --network=none --read-only --entrypoint python3 ... -c 'print(...)'`，输出 `immutable-id-probe-ok`；只证实 Docker 的按 ID、只读、断网探测命令可执行，不证明 amd64/NVIDIA 基础镜像可用。
- 这些检查只核对基础镜像的可观察包版本与准备机 ABI，不证明基础镜像对麒麟内核/驱动/CUDA 算力的实际兼容，也尚未在目标基础镜像中验证 wheel 标签可安装。当前 Docker 清单没有 linux/amd64 的 NVIDIA/vLLM 基础镜像；已有 `mineru:4.0.2` 经 inspect 为 `linux/arm64`（ID `sha256:11a720afc832e5cfaa81722492a77f4a242be6e5fc7a5b6e78902e14b6dd73dd`）。Mac 可用磁盘约 12 GiB，未贸然拉取新的大镜像，也没有真实 Linux wheelhouse、模型权重或物理隔离目标验收；INF-019 保持未完成。
