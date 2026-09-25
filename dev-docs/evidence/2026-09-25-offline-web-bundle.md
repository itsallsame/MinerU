# 业务 Web 离线静态制品包

日期：2026-09-25。范围：P1 `INF-020` 的准备机 Web 产物传输，非麒麟镜像或生产验收。新增 `scripts/offline_web_bundle.py`；与源码 Git bundle、Linux wheelhouse、模型目录分别交付。

设计：从干净的 fork `master` 取精确源码提交，要求 `business-web/dist` 的八个资源逐字节匹配该提交的 `business-web/src` 与自身 `asset-manifest.json`。新输出目录只包含 `dist/`、外层 `manifest.json` 与 `COMPLETE`；外层记录源码提交、Web 清单 SHA-256 与资源数量。接收端先验证包内完整性，再在导入相同源码提交后验证资源与目标源码相符；复制到目标 `business-web/dist` 后，还能核对**实际安装目录**与包相同。符号链接、硬链接、额外文件、旧资源、脏源码、错误提交、已有输出目录或指向别处的“已安装目录”均拒绝。工具不覆盖旧 `dist`，要求部署者显式保留版本化旧目录；包不含模型或 Python wheel。

合成验证：`env PYTHONPATH=. uv run --no-sync pytest -q -o addopts='' tests/business/test_offline_web_bundle.py tests/business/test_release_manifest.py tests/business/test_verify_offline_release.py tests/business/test_offline_configuration.py` 为 47 passed、1 skipped（Docker daemon 相关）；Ruff lint/format 与 feature-list JSON/diff 检查通过。

真实当前 fork 演练：在干净 `1605ca37345ad6148ca5a43cffbd69c1aad487ab` 上运行 `npm run build`，生成八个离线资产；`python3 -m scripts.offline_web_bundle create --repo . --web-dist business-web/dist --output <临时目录>/package` 成功。包目录约 200 KiB（其中 `dist` 约 192 KiB），外层 `manifest.json` SHA-256 为 `0cde95fad392d1cfe5536c51a7c5660e6feb1e5b5a0f8fcab0e1360d61344b75`。从同一提交克隆临时目标后，以 `verify --target-repo` 校验包，复制到目标 `business-web/dist`，再以 `verify --target-repo --installed-dist` 验证，全部成功；目标 Git 工作树仍干净。临时包只是 Mac 演练制品，不是批准介质或正式发布。

边界：清单 SHA-256 与完成标记仅证明内部一致；独立可信渠道仍需交付预期摘要，防止整体替换。此工具未进行麒麟批准介质传输、Linux amd64 镜像构建、容器中 Web 实际运行、物理断网或模型/GPU 验收；后续 `release_manifest.py` 与 `verify_offline_release.py` 仍须把源码、Web、镜像和模型清单联合验证。因此 `INF-020` 和 P1 保持未完成。
