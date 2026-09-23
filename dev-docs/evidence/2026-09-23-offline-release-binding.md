# 离线发布的基础镜像与模型清单绑定

日期：2026-09-23。源码起点：`6159af56`。验证环境：Mac；尚无麒麟 amd64／NVIDIA 实机构建或启动。

## 本轮补强

- `scripts/release_manifest.py` 在核对镜像架构、源码/基础镜像标签后，再检查 worker 和业务镜像的 Docker `RootFS.Layers` 均以前述基础镜像的层链为前缀。仅凭构建者填写的 `org.opencontainers.image.base.id` 标签不再能通过发布记录生成。
- 生产 Compose 要求从当前选定的 `release.json` 中取 `model.manifest_sha256` 填入 `MINERU_EXPECTED_MODEL_MANIFEST_SHA256`。worker 入口脚本强制该值存在，模型预检先比对实际挂载的清单文件 SHA-256，再逐文件核对模型权重及必需仓库。模型仍只读挂载在宿主目录，不进入两个代码镜像。
- 这两项是“错误配置时尽早失败”的校验，不是证明模型与 CUDA、驱动、vLLM 或 MinerU 代码兼容；也不替代操作者把 Compose 镜像引用与发布记录的不可变镜像 ID 逐一核对。

## 验证与边界

- `.venv/bin/python -m pytest -q tests/business/test_offline_configuration.py tests/business/test_release_manifest.py`：13 项通过，覆盖伪造基础 ID 标签但 RootFS 层不一致、缺失层信息、模型清单摘要匹配/不匹配和非法摘要。
- `.venv/bin/ruff check ...` 与 `git diff --check` 通过。
- 用占位的镜像引用和宿主路径运行 `docker compose -f docker/compose.business.yaml config --quiet`：摘要存在时通过；去掉 `MINERU_EXPECTED_MODEL_MANIFEST_SHA256` 后以明确缺参错误失败。这只验证 Compose 解析，不会检查占位镜像、目录或 GPU 是否存在。
- `.venv/bin/python -m pytest -q tests/business`：100 项通过、2 个依赖弃用警告；Mac 测试不能替代完全断网的麒麟 x86_64＋NVIDIA 现场验收。P1 功能清单不因模拟校验而标成生产通过。
