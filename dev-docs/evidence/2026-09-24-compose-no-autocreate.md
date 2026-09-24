# 离线 Compose 挂载不自动创建宿主路径（Mac，2026-09-24）

## 发现与修正

Mac 上将原 `docker/compose.business.yaml` 渲染为 JSON 时，六个短写法 bind 卷都带有 `bind.create_host_path: true`。即使流程要求先运行宿主路径核验，操作员若跳过该步骤或路径拼错，Compose 仍可能先创建空目录；模型目录和清单尤其不应由部署工具代建。

现改为六个显式 bind 卷，全部设置 `create_host_path: false`。worker 的模型目录、模型清单和共享原文件仍为只读，业务 API 仅对业务库与共享原文件可写。测试同时检查源 YAML 与实际 `docker compose config --format json` 的解析结果，并确认仅执行 config 不会创建临时宿主路径。

## 验证与边界

`test_offline_configuration.py` 11/11，关联业务服务测试合计 16/16；完整 Mac 业务套件 192 passed、1 skipped，Ruff 通过。Docker Compose config 实际渲染成功，内部网络仍为 `internal: true`。这只证明配置形状，不证明目标麒麟的 Compose 版本、真实挂载权限、NVIDIA 设备或模型加载；P1 保持未完成。

同轮检查 Doclib 队列可见同内容/档位的解析批次复用；尚无安全的消费者引用取消契约，因此未增加会误导用户以为 GPU 已停的取消按钮。FE-016 仍按现有设计文档待实现。
