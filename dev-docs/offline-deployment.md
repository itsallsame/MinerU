# 麒麟离线交付草案（P1 进行中）

状态：完成源码/配置骨架与 Mac 单测；**尚未在麒麟或 NVIDIA 上构建、启动或验收**。

## 制品边界

- 代码镜像：从本 fork 的确定提交分别构建 `docker/worker/Dockerfile` 与 `docker/business-api/Dockerfile`。二者都只复制源码和预先准备的 `wheelhouse/`，没有模型权重；业务镜像还复制由 `business-web/` 构建的静态 `dist/`，并设置 `MINERU_BUSINESS_WEB_ROOT=/opt/mineru/business-web/dist`，不依赖 Python 安装后文件所在路径推算页面位置。依赖安装层在源码复制前，源码变更通常只产生较小的后续层。必须提供已导入本机、与目标驱动兼容的 amd64 NVIDIA/vLLM 基础镜像；构建命令使用 `--pull=false --network=none`。当前两个 Dockerfile 可共用这一基础镜像及锁定依赖，但业务 API 不挂模型、不申请 GPU。
- 模型：在联网准备机按目标 Torch + vLLM 组合取得完整权重，生成 `model-manifest.json`，通过批准介质分别导入麒麟宿主目录。`compose.business.yaml` 将模型和清单只读挂载；启动时先比对清单本身与所选发布记录的 SHA-256，再逐文件验证模型，并检查 Torch 与 vLLM 两个必需模型仓库的 MinerU 完整标记。更换模型不需要重建代码镜像，但必须重新生成清单和发布记录并做回归。
- 运行数据：Doclib 的 `MINERU_HOME` 持久挂载；共享原文件目录 `/srv/mineru-inbox` 在 worker 中只读、业务 API 中可写，且必须是**同一宿主目录与同一容器绝对路径**。业务 SQLite 目录另行持久挂载，三者都不进入代码镜像。业务 API 启动时只初始化新的业务库、校验已有上传目录，并以显式内部 URL 连接 Doclib；不在启动时要求 worker 已可用。
- 业务证据引用历史 parse ID。Doclib 默认压缩可能合并/删除旧批次；本 Compose 将 `MINERU_DOCLIB_COMPACTION_INTERVAL_SEC=0`，保留历史解析文件以供版本绑定证据读取。需要监测 Doclib 目录增长，并把业务 SQLite、原文件和 Doclib 目录做一致备份；不得在业务修订仍引用时手动清理相关批次。
- `wheelhouse/`、模型权重、真实文件、数据库和私密配置不进 Git。构建后用 `scripts/release_manifest.py` 将源码提交、基础/worker/业务 API 三个镜像 ID、wheelhouse 逐文件哈希、模型清单哈希和上一个发布清单关联起来；该清单应保存在独立发布目录，用于复核与回退。

## 准备与验证步骤

1. 在联网的 **Linux amd64** 准备机锁定 MinerU 提交、Python 版本和目标基础镜像摘要。准备环境必须与基础镜像的 Python ABI 一致；从该镜像导出 `torch==...`、`torchvision==...`、`vllm==...` 三项精确版本到约束文件，再设置 `MINERU_BASE_CONSTRAINTS` 指向它并运行 `sh scripts/prepare-worker-wheelhouse.sh`。脚本生成带哈希的 `wheelhouse/requirements.lock` 和全部目标架构 wheel；不把 Mac 解析出的依赖当作 Linux 锁。当前尚无真实 Linux 制品，不能视为已完成。
2. 准备完整模型目录后运行：`python3 scripts/offline_package.py create-manifest --model-dir /path/to/models --manifest /path/to/model-manifest.json`。该命令只生成哈希清单，不负责下载模型或判断 MinerU 模型组合是否正确。
3. 在开发/准备机执行 `cd business-web && pnpm test && pnpm build`，只需预置 Node/pnpm，无第三方前端包；传输 `business-web/dist/` 时核对其中 `asset-manifest.json` 与全部静态文件的 SHA-256。计算该 manifest 文件本身的 SHA-256，作为业务镜像构建参数 `WEB_ASSET_MANIFEST_SHA256`。前端源码属于 Git 提交，构建产物独立校验和传输，不依赖麒麟在线安装前端依赖。
4. 在麒麟隔离区逐项校验导入文件哈希，再分别构建两个代码镜像。示例 worker 命令：`docker build --pull=false --network=none -f docker/worker/Dockerfile --build-arg BASE_IMAGE=<已导入的精确镜像引用> --build-arg BASE_IMAGE_ID=<docker image inspect 得到的基础镜像 ID> --build-arg SOURCE_REVISION=<源码提交> -t <本地worker标签> .`；业务 API 将 `-f` 改为 `docker/business-api/Dockerfile`，标签改为业务 API 标签，并额外传 `--build-arg WEB_ASSET_MANIFEST_SHA256=<前端manifest的SHA256>`。尖括号项必须替换为真实值；不能使用浮动标签做正式发布。Dockerfile 会校验前端 manifest 哈希，再按锁文件/哈希安装依赖、以 `--no-deps` 安装本 fork 源码。
5. 确保 Git 工作树干净后，执行 `python3 scripts/release_manifest.py --worker-image <worker镜像> --business-image <业务API镜像> --base-image <基础镜像> --wheelhouse wheelhouse --model-manifest /path/to/model-manifest.json --web-dist business-web/dist --output /path/to/release.json`。工具拒绝任一镜像非 `linux/amd64`、两个代码镜像源码/基础镜像标签不一致、镜像层不继承所检基础镜像、前端制品与业务镜像标签不一致、错误地复用同一镜像 ID 或缺少离线制品；回退发布可再传 `--previous-release`。当前只有纯函数和 CLI 模拟测试，尚无真实 amd64 镜像的发布清单。
6. 在麒麟隔离区导入镜像与模型后、启动 Compose 前，从本 fork 的干净源码目录运行 `python3 -m scripts.verify_offline_release --release /受控发布目录/release.json --worker-image <已导入worker镜像> --business-image <已导入业务镜像> --base-image <已导入基础镜像> --wheelhouse /离线目录/wheelhouse --model-manifest /模型目录外/model-manifest.json --model-dir /模型目录 --web-dist business-web/dist --source-tree . --output /独立验收目录/artifact-verification.json`。存在前一发布清单时再传 `--previous-release`。该命令重新从已导入镜像读取不可变 ID、架构、标签和基础层链，逐文件复核 wheelhouse、Web 制品及真实模型目录，报告只记 ID、哈希和数量，不收录权重或业务正文。任何失败都不得进入部署；目前只有模拟 Docker inspect 的 Mac 单测，尚无目标机报告。
7. 设置 `MINERU_WORKER_IMAGE`、`MINERU_BUSINESS_IMAGE`、`MINERU_MODELS_HOST_DIR`、`MINERU_MODEL_MANIFEST_HOST_FILE`、`MINERU_DOCLIB_HOST_DIR`、`MINERU_SHARED_DOCUMENTS_HOST_DIR`、`MINERU_BUSINESS_HOST_DIR`，并从**当前选定的** `release.json` 的 `model.manifest_sha256` 设置 `MINERU_EXPECTED_MODEL_MANIFEST_SHA256`。先运行 `python3 -m scripts.verify_host_layout --model-dir "$MINERU_MODELS_HOST_DIR" --model-manifest "$MINERU_MODEL_MANIFEST_HOST_FILE" --doclib-dir "$MINERU_DOCLIB_HOST_DIR" --shared-documents-dir "$MINERU_SHARED_DOCUMENTS_HOST_DIR" --business-dir "$MINERU_BUSINESS_HOST_DIR"`；所有目录和模型清单必须已存在，且各目录在解析符号链接后不得相同或互相包含，清单必须在这些目录之外。否则 business API 的可写挂载可能绕过 worker 的只读模型挂载。之后运行 `docker compose -f docker/compose.business.yaml config` 检查配置。清单与发布记录摘要不一致时 worker 启动失败；Compose 配置通过也不替代第 6 步制品核验。可设置 `MINERU_BUSINESS_MAX_UPLOAD_BYTES`、`MINERU_BUSINESS_BIND_ADDRESS` 和 `MINERU_BUSINESS_PORT`；默认仅将开放业务 Web/API 绑定宿主 `127.0.0.1:8088`，Doclib 不发布宿主端口。系统没有登录、用户或权限层；改为内网 IP 之前必须确认物理隔离和可达范围。仅在目录和文件权限核对后启动。
8. 在物理断网情况下启动容器，然后运行 `python3 -m scripts.verify_business_runtime --release /受控发布目录/release.json --model-dir /模型目录 --model-manifest /模型目录外/model-manifest.json --business-bind 127.0.0.1 --business-port 8088 --output /独立验收目录/runtime-preflight.json`。如果第 7 步改变了 `MINERU_BUSINESS_BIND_ADDRESS` 或端口，这里必须使用同一值；可用 `--compose-file` 指定非默认 Compose 文件。工具检查实际容器与所选镜像 ID、只读根文件系统、模型/数据挂载及宿主路径互不重叠、单一内部网络、仅业务端口对外、离线环境变量、业务 API、内部 Doclib 状态、Docker GPU 请求、容器内 CUDA 与宿主 `nvidia-smi`。只允许私网或回环业务绑定，Doclib 不得发布宿主端口。失败返回非零且不写成功报告；报告不含模型路径或业务正文。运行前仍必须完成第 6 步的制品复核；此检查不证明物理断网、权重能被 vLLM 完整加载或解析质量。
9. 继续验收模型加载、真实 PDF/图片和四类标注样本、服务重启、备份与回退。第 6 步只证明制品一致，第 8 步只证明启动时配置和基础 GPU/服务可用性，两者均不能替代端到端解析与目标机性能测试。目前只有 Mac 模拟单测，尚无麒麟/NVIDIA 现场结果。

回退时须保留上一版的发布清单、基础/代码镜像、wheelhouse、模型目录和模型清单；先核对清单哈希，再切回上一版镜像与模型挂载并复测。业务数据库的 schema 回退策略将在 P3 持久化设计时确定，不能仅凭替换镜像宣称可回滚。

## 当前已知限制

- Docker daemon 经授权后可访问；现有 `mineru:4.0.2` 是 **Linux arm64**，不能充当麒麟 amd64 基础镜像。没有真实 `docker build`/`up` 证据；amd64 wheelhouse、目标基础镜像和模型也尚未准备。
- 当前 Compose 包含开放业务 Web/API 与内部 Doclib/GPU worker；业务 Web 已有复核/成果、模板管理、历史块树与块级候选检索，但仍缺四类真实样本验收。Mac 已做同源页面和 Playwright 合成数据回归，运行态预检仅有模拟单测；Compose 宿主端口映射、内部网络及跨容器 Doclib 通信仍须在目标环境实测。API 的 `UploadFile` 应用层限额不等于入口请求体限额；正式发布前需加反向代理或入口层体积限制，并测试超限行为。
- 上游本地模型读取路径原先会创建 `.locks`；fork 已加入不写锁的 `source=local` 分支，其相关单测已在 Mac 开发环境通过。只读挂载容器本身仍待实测。
- 模型清单和必需仓库检查只证实文件一致及 MinerU 标记齐全，不证实权重与源码、GPU 驱动或 vLLM 兼容。生产验收必须覆盖这些组合。
