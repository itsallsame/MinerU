# 麒麟离线交付草案（P1 进行中）

状态：仅完成源码/配置骨架与 Mac 上的无模型测试；**尚未在麒麟或 NVIDIA 上构建、启动或验收**。

## 制品边界

- 代码镜像：从本 fork 的确定提交构建，`docker/worker/Dockerfile` 只复制源码和预先准备的 `wheelhouse/`。必须提供已导入本机、与目标驱动兼容的 amd64 NVIDIA/vLLM 基础镜像；构建命令使用 `--pull=false --network=none`。不在构建时下载权重。
- 模型：在联网准备机按目标 Torch + vLLM 组合取得完整权重，生成 `model-manifest.json`，通过批准介质分别导入麒麟宿主目录。`compose.business.yaml` 将模型和清单只读挂载；启动时逐文件 SHA-256 验证。更换模型不需要重建代码镜像，但必须重新生成清单并做回归。
- 运行数据：Doclib 的 `MINERU_HOME` 持久挂载。将来的业务数据目录需单独挂载，不能跟模型或代码镜像混在一起。
- `wheelhouse/`、模型权重、真实文件、数据库和私密配置不进 Git。实际交付要对基础镜像、wheelhouse、代码镜像和模型分别记录哈希。

## 准备与验证步骤

1. 在联网的 **Linux amd64** 准备机锁定 MinerU 提交、Python 版本、目标基础镜像摘要和 `.[full]` 依赖解析结果；准备包含构建后端、全部传递依赖及目标架构 wheel 的 `wheelhouse/`。当前仓库尚无可重现的锁文件/下载脚本，故不能把 `wheelhouse/` 视为已完成制品。
2. 准备完整模型目录后运行：`python3 scripts/offline_package.py create-manifest --model-dir /path/to/models --manifest /path/to/model-manifest.json`。该命令只生成哈希清单，不负责下载模型或判断 MinerU 模型组合是否正确。
3. 在麒麟隔离区逐项校验导入文件哈希，再构建镜像：`docker build --pull=false --network=none -f docker/worker/Dockerfile --build-arg BASE_IMAGE=<已导入的精确镜像引用> --build-arg SOURCE_REVISION=<源码提交> -t <本地代码镜像标签> .`。尖括号项必须替换为真实值；不能使用浮动标签做正式发布。
4. 设置 `MINERU_WORKER_IMAGE`、`MINERU_MODELS_HOST_DIR`、`MINERU_MODEL_MANIFEST_HOST_FILE`、`MINERU_DOCLIB_HOST_DIR` 后运行 `docker compose -f docker/compose.business.yaml config` 检查配置。仅在目录和文件权限核对后启动。
5. 在物理断网情况下启动容器，检查预检结果、Doclib 可用性、模型加载、解析真实 PDF/图片、服务重启与回退。当前只验证了 Compose 语法和轻量预检测试，尚无此项结果。

## 当前已知限制

- Docker daemon 在当前会话不可访问；没有真实 `docker build`/`up` 证据。`wheelhouse/` 与基础镜像也尚未准备。
- 当前 Compose 只包含内部 Doclib/GPU worker；业务 Web/API 尚未开发，内部 Doclib 客户端跨容器连接需在 P2 契约原型中验证。
- 上游本地模型读取路径原先会创建 `.locks`；fork 已加入不写锁的 `source=local` 分支，但其上游单测尚未在安装完整 MinerU 依赖的环境执行。
- 模型清单只证实文件字节一致，不证实权重与源码、GPU 驱动或 vLLM 兼容。生产验收必须覆盖这些组合。
