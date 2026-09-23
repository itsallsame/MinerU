# 麒麟离线交付草案（P1 进行中）

状态：完成源码/配置骨架与 Mac 单测；**尚未在麒麟或 NVIDIA 上构建、启动或验收**。

## 制品边界

- 代码镜像：从本 fork 的确定提交构建，`docker/worker/Dockerfile` 只复制源码和预先准备的 `wheelhouse/`。必须提供已导入本机、与目标驱动兼容的 amd64 NVIDIA/vLLM 基础镜像；构建命令使用 `--pull=false --network=none`。不在构建时下载权重。
- 模型：在联网准备机按目标 Torch + vLLM 组合取得完整权重，生成 `model-manifest.json`，通过批准介质分别导入麒麟宿主目录。`compose.business.yaml` 将模型和清单只读挂载；启动时逐文件 SHA-256 验证，并检查 Torch 与 vLLM 两个必需模型仓库的 MinerU 完整标记。更换模型不需要重建代码镜像，但必须重新生成清单并做回归。
- 运行数据：Doclib 的 `MINERU_HOME` 持久挂载。将来的业务数据目录需单独挂载，不能跟模型或代码镜像混在一起。
- `wheelhouse/`、模型权重、真实文件、数据库和私密配置不进 Git。构建后用 `scripts/release_manifest.py` 将源码提交、基础/worker 镜像 ID、wheelhouse 逐文件哈希、模型清单哈希和上一个发布清单关联起来；该清单应保存在独立发布目录，用于复核与回退。

## 准备与验证步骤

1. 在联网的 **Linux amd64** 准备机锁定 MinerU 提交、Python 版本和目标基础镜像摘要。准备环境必须与基础镜像的 Python ABI 一致；从该镜像导出 `torch==...`、`torchvision==...`、`vllm==...` 三项精确版本到约束文件，再设置 `MINERU_BASE_CONSTRAINTS` 指向它并运行 `sh scripts/prepare-worker-wheelhouse.sh`。脚本生成带哈希的 `wheelhouse/requirements.lock` 和全部目标架构 wheel；不把 Mac 解析出的依赖当作 Linux 锁。当前尚无真实 Linux 制品，不能视为已完成。
2. 准备完整模型目录后运行：`python3 scripts/offline_package.py create-manifest --model-dir /path/to/models --manifest /path/to/model-manifest.json`。该命令只生成哈希清单，不负责下载模型或判断 MinerU 模型组合是否正确。
3. 在麒麟隔离区逐项校验导入文件哈希，再构建镜像：`docker build --pull=false --network=none -f docker/worker/Dockerfile --build-arg BASE_IMAGE=<已导入的精确镜像引用> --build-arg BASE_IMAGE_ID=<docker image inspect 得到的基础镜像 ID> --build-arg SOURCE_REVISION=<源码提交> -t <本地代码镜像标签> .`。尖括号项必须替换为真实值；不能使用浮动标签做正式发布。Dockerfile 先按锁文件/哈希安装依赖，再以 `--no-deps` 安装本 fork 源码；发布工具核对构建标签与实际镜像 ID。
4. 确保 Git 工作树干净后，执行 `python3 scripts/release_manifest.py --worker-image <worker镜像> --base-image <基础镜像> --wheelhouse wheelhouse --model-manifest /path/to/model-manifest.json --output /path/to/release.json`。工具拒绝非 `linux/amd64` 镜像、源码标签不一致或缺少离线制品；回退发布可再传 `--previous-release`。当前只有纯函数测试，尚无真实 amd64 镜像的发布清单。
5. 设置 `MINERU_WORKER_IMAGE`、`MINERU_MODELS_HOST_DIR`、`MINERU_MODEL_MANIFEST_HOST_FILE`、`MINERU_DOCLIB_HOST_DIR` 后运行 `docker compose -f docker/compose.business.yaml config` 检查配置。仅在目录和文件权限核对后启动。
6. 在物理断网情况下启动容器，检查预检结果、Doclib 可用性、模型加载、解析真实 PDF/图片、服务重启与回退。当前只验证了 Compose 语法和 Mac 单测，尚无此项结果。

回退时须保留上一版的发布清单、基础/代码镜像、wheelhouse、模型目录和模型清单；先核对清单哈希，再切回上一版镜像与模型挂载并复测。业务数据库的 schema 回退策略将在 P3 持久化设计时确定，不能仅凭替换镜像宣称可回滚。

## 当前已知限制

- Docker daemon 经授权后可访问；现有 `mineru:4.0.2` 是 **Linux arm64**，不能充当麒麟 amd64 基础镜像。没有真实 `docker build`/`up` 证据；amd64 wheelhouse、目标基础镜像和模型也尚未准备。
- 当前 Compose 只包含内部 Doclib/GPU worker；业务 Web/API 尚未开发，内部 Doclib 客户端跨容器连接需在 P2 契约原型中验证。
- 上游本地模型读取路径原先会创建 `.locks`；fork 已加入不写锁的 `source=local` 分支，其相关单测已在 Mac 开发环境通过。只读挂载容器本身仍待实测。
- 模型清单和必需仓库检查只证实文件一致及 MinerU 标记齐全，不证实权重与源码、GPU 驱动或 vLLM 兼容。生产验收必须覆盖这些组合。
