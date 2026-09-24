# 仅模型更新的离线发布验证（Mac 合成制品）

日期：2026-09-24。范围：INF-027 的发布记录和制品边界；**不是**真实权重、Docker 镜像或麒麟/NVIDIA 验收。

## 验证内容

- 从同一源码提交、基础/worker/业务镜像模拟 inspect 记录、wheelhouse、Web 制品及第一版模型目录生成旧发布记录。
- 将模型复制到新的宿主目录，改变一份模拟权重和其精确仓库提交，重新生成来源锁、逐文件清单及关联旧版哈希的新发布记录。
- 断言源码提交、三个镜像不可变 ID、wheelhouse 和 Web 制品记录均不变，只有模型清单身份发生变化；导入制品校验接受新记录与新模型目录，旧记录与旧模型目录仍可独立通过；新记录搭配旧模型目录被拒绝。

执行：`PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider -o addopts= tests/business/test_verify_offline_release.py::test_model_only_release_reuses_code_images_and_keeps_prior_models`，结果 1 passed。完整 `tests/business` 结果 343 passed、2 skipped、2 个第三方依赖警告。这是一条合成回归测试，不下载权重，也没有执行真实 Docker build。

## 尚未验证

本机 Docker daemon 未响应，实际构建上下文检查按预设超时跳过。尚无 Linux amd64/NVIDIA 基础与代码镜像、真实新模型目录、隔离介质传输、容器只读挂载、模型重载和四类真实标注样本。因此 INF-027 保持未完成。只有当新权重与既有源码和 Torch/CUDA/vLLM/推理接口兼容时才能走“仅模型更新”；否则要同步构建新的代码镜像。实际操作和回退边界见 [麒麟离线交付草案](../offline-deployment.md#仅模型更新不重建代码镜像)。
