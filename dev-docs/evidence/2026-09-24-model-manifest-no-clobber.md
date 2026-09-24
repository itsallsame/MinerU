# 离线模型清单版本不可覆盖（Mac 回归）

模型权重独立于代码镜像，发布与回退依赖每一版模型目录和对应清单的稳定配对。原 `create-manifest` 用 `write_text` 覆盖输出；当操作者复用路径时，旧清单会被新模型摘要替换，已选择的旧发布将失去可复核依据。

现在 `scripts/offline_package.py create-manifest` 只接受新输出路径，拒绝已有文件/符号链接和模型目录内路径；在同目录落盘临时文件后以仅在目标不存在时成功的硬链接发布，再清理临时文件。部署步骤要求每版模型使用独立清单路径并保留旧目录与旧清单。并发写入者先占据目标时，工具失败且不覆盖其文件。

验证：

- `.venv/bin/python -m pytest -q tests/business/test_offline_configuration.py`：10 通过，覆盖旧清单不变、新版独立创建、符号链接、模型目录内路径和并发抢占。
- `PYTHONPATH=. .venv/bin/pytest -q tests/business`：182 通过、1 个 opt-in 跳过、2 个依赖弃用警告；本地 Unix/TCP 测试获准绑定后通过。
- `.venv/bin/ruff check scripts/offline_package.py tests/business/test_offline_configuration.py` 与 `git diff --check`：通过。

范围限制：Mac 单测只验证生成工具的文件语义，不证明目标机模型真实可用、权重与 4.0/Torch/vLLM/CUDA 兼容、物理断网或回退演练。P1 目标机门禁继续未完成；系统仍无用户或权限层。
