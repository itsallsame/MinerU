# 当前 fork 的业务代码增量离线导入演练

日期：2026-09-25。范围：INF-026 的**源码传输**部分，非镜像构建或麒麟目标验收。准备端为 macOS 上干净的 `itsallsame/MinerU` `master`；旧版精确提交 `b7f7ed50a1aa7cc07129af23591cef6053270455`，新版 `91287602dc44b563033790b19b7882437d2a713c`。此区间包含 `business-web/src`、`mineru/business/api`、`mineru/business/store` 的实际业务代码变更，不只是文档/测试。

在仓库外新建临时目录，运行 `python3 -m scripts.offline_source_bundle create --repo . --previous-revision b7f7ed50a1aa7cc07129af23591cef6053270455 --reuse-wheelhouse --output <临时目录>/business-code-update`。生成的 `source.bundle` 为约 12 KiB；`manifest.json` SHA-256 为 `29b2bf9ef5aeeb821f60f46745b2695cbad27ca0fbf556bc13908ecdc647c576`。两提交间 `pyproject.toml` 和 `docker/worker/build-requirements.in` 未变，因此此工具允许保留已有 wheelhouse；这不证明 wheelhouse 字节或目标 ABI 正确。

在另一个临时目录准备干净、位于上述旧提交的 `master` 检出，执行 `python3 -m scripts.offline_source_bundle verify --bundle-dir <临时目录>/business-code-update --target-repo <临时目标>`，通过后以该包 `git fetch`、`git merge --ff-only FETCH_HEAD`。目标最终 HEAD 精确等于 `91287602dc44b563033790b19b7882437d2a713c`，工作树干净。工具检查增量 Git 对象，拒绝模型权重/常见模型目录；包只包含源码增量，不包含宿主模型目录或 wheelhouse。首次用 `5af6dc6f…` → `91287602…` 测试了约 4 KiB 的文档/测试增量，随后改用包含真实业务代码的上述区间作为主要证据。

定向回归 `env PYTHONPATH=. uv run --no-sync pytest -q -o addopts='' tests/business/test_offline_source_bundle.py tests/business/test_offline_configuration.py` 为 24 passed、1 skipped；跳过项需要可用 Docker daemon。JSON 与 diff 检查通过。

这说明当前 fork 的**代码增量传输与导入路径**可在 Mac 上运行，且不需要把宿主模型权重加入源码包。它没有运行 `docker build`、`docker save/load`、Compose、真实模型加载或 GPU 推理。当前 Mac Docker daemon 的只读 `docker info` 查询无响应，主动中止；无麒麟 Linux amd64＋NVIDIA 机器、已导入基础镜像、目标 wheelhouse 和真实模型权重，因此 INF-026/P1 仍不标完成。实际隔离区应按 `dev-docs/offline-deployment.md` 先复核独立渠道传来的清单摘要，再导入并重建需要更新的代码镜像；模型目录继续作为独立只读挂载，不得用本次临时包替代正式发布制品。
