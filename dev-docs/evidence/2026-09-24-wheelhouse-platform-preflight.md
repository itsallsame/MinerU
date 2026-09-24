# wheelhouse 在目标基础镜像内的离线预检（2026-09-24）

## 缺口与实现

仅匹配基础镜像已安装包的版本号和 Python 主次版本，仍不能证明准备机下载的 `.whl` 可由目标基础镜像选择；平台标签、glibc 或包哈希不符可能直到隔离区构建才失败。`scripts/prepare-worker-wheelhouse.sh` 现在在暂存目录完成带哈希锁解析和 wheel 下载后、发布 `wheelhouse/` 前，以同一个不可变基础镜像 ID 启动一次容器。容器不拉取、不联网，根文件系统只读，暂存目录只读挂载；执行 `pip install --dry-run --ignore-installed --no-deps --no-index --require-hashes`，只从暂存 wheel 中为锁文件各项选择兼容候选并核对哈希。失败时退出并保留未发布的暂存目录供检查，原有 wheelhouse 不受影响。

`--no-deps` 有意只证明锁文件中每一个显式条目能从本地 wheelhouse 解析，不声称运行时依赖闭包正确；实际镜像构建仍按原 Dockerfile 在断网状态执行安装，之后必须做运行态和 GPU/模型加载验收。

## 验证与限制

- Shell 语法检查、Mac 模拟 Linux 准备流程的成功/失败测试和静态调用顺序断言通过。新增负例证明镜像内 wheel 预检失败时已下载的暂存 wheel 留存，但不发布 `wheelhouse/`。
- 完整 Mac 业务测试 241 通过、1 跳过；Ruff 和 `git diff --check` 通过。在本机现有 arm64 镜像中用空需求文件执行同一组只读、断网、不可变 ID、bind 卷和 pip dry-run 参数成功，只证明命令语法与容器执行路径成立；未以真实 wheelhouse 做平台选择。
- 未使用真实 linux/amd64 NVIDIA/vLLM 基础镜像执行此预检；现有 Mac Docker 镜像仍只有 arm64 MinerU 4，且没有目标麒麟机器，因此 INF-019、INF-021、INF-029 和 QA-029 仍不标完成。
