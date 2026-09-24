# Docker 构建上下文的模型边界（2026-09-24）

本轮审查发现 `.dockerignore` 虽以白名单起步，但 `!mineru/**` 放行整个源码树，原有防御只排除若干权重后缀。误放在 `mineru/models/config.json`、`mineru/weights/tokenizer.json` 或 `mineru/tokenizer.model` 的模型资料可能随本地构建上下文发送，违反代码与宿主模型分离的交付意图。

现补常见模型目录和数据后缀排除规则，放在所有放行规则之后。Docker 官方说明 `.dockerignore` 可排除目录、`**` 可匹配任意层级，且最后匹配的规则决定包含与否：[Build context 文档](https://docs.docker.com/build/concepts/context/)。源码增量包另有独立的模型目录/后缀拒绝规则；两者分别防止 Git 交付和本地构建上下文误混入。业务 API 容器仍不挂模型、不申请 GPU；worker 只读挂宿主模型目录。

验证：`test_offline_configuration.py` 13 项通过；完整 Mac 业务套件 317 项通过、2 项跳过、2 项依赖警告。真实 Docker build-context 测试因本机 daemon 超时按预设条件跳过。新增静态断言确认排除规则位于 `!mineru/**` 之后，并扩展实际 context 测试夹具，待 Docker 可用时必须重新执行。完整套件首次运行暴露了 Doclib 契约测试的时序假设：后台摄入可能先排队，首次 `force` 请求合法复用活跃批次；测试现改为断言首轮等待 ID 和下一轮新建 ID 不相交，生产逻辑未改。当前工作树未发现模型目录或新增模型后缀文件。静态规则与夹具不能冒充真实 BuildKit 结果，更不能证明目标麒麟 amd64/NVIDIA 镜像、权重和运行质量。离线部署文档的当前业务库 schema 已同步为 12。
