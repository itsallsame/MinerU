# 离线代码镜像构建上下文白名单（Mac，2026-09-24）

## 风险与边界

旧 `.dockerignore` 是已知目录/扩展名黑名单。若开发者把模型放在新命名目录，Docker 构建会先将其发送到 daemon，即使 Dockerfile 没有 `COPY` 权重。这违背“业务代码更新不重传模型”的交付约束，也可能暴露业务原件。

现在构建上下文只允许 fork 的 `mineru/` 源码、`pyproject.toml`、README/许可证、worker 入口与 Dockerfile、`scripts/offline_package.py`、`business-web/dist/`、`wheelhouse/requirements.lock` 和 `.whl`。可允许树内仍额外排除权重、数据库、文档、环境文件等常见扩展名。模型与清单继续从独立宿主目录只读挂载，不进入代码镜像；业务原件和数据也只从持久宿主目录挂载。

## 实测

新增一个无基础镜像、无网络的 Docker scratch 构建测试，使用同一 `.dockerignore` 和合成上下文，把整个被允许的上下文 `COPY` 到临时输出。它确认必需源码/Web/wheel 被传入，而新命名模型目录、客户文档目录、私密配置、额外脚本、前端源码、wheelhouse 杂项，以及误放进 `mineru/` 的权重和 SQLite 都未传入。第一次实测发现仅靠根目录 `*` 加父目录反选仍放行 `scripts/other.py`；补上父目录下逐层排除后复测通过。

精确特性命令 `.venv/bin/python -m pytest tests/business/test_offline_configuration.py` 为 12/12；完整 Mac 业务测试 193 passed、1 skipped，Ruff 和差异检查通过。INF-004～INF-007 的“构建上下文排除”已由真实 Docker 语义证明；它们不代表目标麒麟 amd64 代码镜像已经构建，也不证明宿主目录权限或物理隔离。目标镜像、GPU、模型加载和四类真实样本仍是 P1/P6 未完成门槛。
