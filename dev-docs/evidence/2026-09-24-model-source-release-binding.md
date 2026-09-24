# 模型来源锁与离线发布记录绑定（Mac 模拟验证）

承接 `2026-09-24-pinned-model-preparation.md`：只拥有模型目录的文件哈希还不足以让交付人员直接核对本版选择的两个上游模型提交。本次发布记录从模型目录的 `.mineru_source_lock.json` 读取固定的 Torch 与 vLLM Hugging Face 仓库 ID、40 位提交 SHA 和各自文件数，并要求来源锁自身的字节哈希在 `model-manifest.json` 中。导入后的 `verify_offline_release` 使用实际挂载模型目录中的来源锁重建发布记录，再逐文件核验目录；缺锁、篡改、错误仓库/提交和文件数不符都拒绝。

发布命令现在必须提供 `--model-source-lock /模型目录/.mineru_source_lock.json`；发布记录升为 schema 4，制品验收、运行态预检和业务状态备份读取方同步要求 schema 4。此前未上线的 schema 3 原型不做迁移。这不把权重放进代码镜像，也不引入登录、用户或权限管理。来源锁与清单不是数字签名：仍需通过独立可信渠道核对发布清单、批准介质交接，并在麒麟 x86_64/NVIDIA 上做真实模型加载与样本回归。

验证：纯文件/模拟镜像定向测试 97 通过、1 跳过，覆盖准备工具生成的来源锁可被发布校验器接受，以及发布和导入阶段的缺锁/篡改/错误仓库/提交/文件数拒绝、备份拒绝旧 schema；直接脚本入口 `python scripts/release_manifest.py --help` 可用。schema 4 下最终完整业务回归 296 通过、2 跳过、2 条既有依赖警告；Ruff lint 与 `git diff --check` 通过。Docker 构建上下文测试在本机 daemon 不响应/不可用时明确跳过，不据此宣称镜像构建通过。当前无真实 amd64 镜像、模型权重或目标机验收，相关 P1/P6 特性不标完成。
