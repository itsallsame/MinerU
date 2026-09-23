# 麒麟业务运行态预检（代码与模拟验证）

状态：预检工具已实现，**无麒麟 amd64/NVIDIA 现场运行报告**。本记录不能当作断网启动、真实解析或驱动兼容验收。

新增 `scripts/verify_business_runtime.py`，从所选发布清单、Docker Compose 当前容器和内部服务状态核对：两个代码镜像 ID 与运行状态、只读根文件系统、模型仅在 worker 只读挂载、业务/worker 原文件目录一致、单一内部网络、Doclib 无宿主发布端口、业务端口仅私网/回环绑定、离线环境变量、业务能力接口、Doclib 状态与远程解析禁用、Docker GPU 请求、容器内 CUDA 与宿主 NVIDIA 驱动查询。输出报告仅含版本、镜像 ID、网络名、端口和 GPU 数量；不写模型路径、原文件或解析正文。所有失败返回非零且不写成功报告。部署顺序和命令已加入 `offline-deployment.md`。

验证：`.venv/bin/python -m pytest tests/business/test_verify_business_runtime.py -q --no-cov` 为 23/23；新增文件 Ruff check/format 及 `git diff --check` 通过；CLI `--help` 可用。案例包含正确部署形状与镜像、挂载、内网、端口、离线配置、远程解析、CUDA/宿主 GPU 异常；特意禁止 `0.0.0.0` 和 `::` 全网绑定。

全业务套件第一次运行是 150 通过、1 失败（新增测试扩展前）；单独复跑仍失败：`test_repo_paper_pdf_round_trip_through_real_business_api_and_doclib` 的任务以 `parse_batch_invalid` 结束。该测试走 Mac 本地 Doclib 示例 PDF，并非本工具代码路径；尚未确认根因，不能把全业务回归写为通过。其他 150 项通过不抵消该失败。

边界与下一步：预检不验证发布清单的文件哈希，运行前必须独立执行 `verify_offline_release`；它也不证明物理断网、vLLM 完整加载、解析准确率、显存/吞吐、备份回退。获得批准的 Linux amd64 离线镜像、模型和麒麟 NVIDIA 机器后，先执行制品复核，再在断网环境启动并运行此工具，随后做真实四类样本和故障演练。另需调查 Mac 示例 PDF 的 `parse_batch_invalid` 回归。
