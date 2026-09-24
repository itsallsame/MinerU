# 离线状态备份的全局容器写入检查

日期：2026-09-24。范围：`scripts.business_state_backup` 的停机前置条件；未在麒麟目标机执行。

旧流程只读取所选 Compose 项目的 `business-api`、`doclib-worker` 状态。另一个 Compose 项目或普通 Docker 容器若以可写方式挂载业务 SQLite、Doclib 或共享原件目录，仍可能在复制时写入；逐文件复制前后哈希相同也不能证明跨目录同一逻辑时点。

现在 `create` 与 `restore` 在所选服务停机检查之后，读取本机全部运行容器 ID 和 inspect 挂载清单，拒绝与受保护目录重叠的可写 bind/volume（含父目录挂载）。`restore` 还保护备份目录及尚未存在的新目标目录。Docker/inspect 不可用、清单不完整或容器挂载结构异常时失败关闭；只读挂载和不重叠的可写挂载可通过。没有删除或修改任何容器或业务数据。

定向 `tests/business/test_state_backup.py`：11 passed，覆盖另一容器可写父目录、只读/不重叠挂载、新恢复目标、inspect 失败。完整 `tests/business`：348 passed、2 skipped、2 个第三方依赖警告；Ruff lint、特性清单 JSON 与差异检查通过。该检查只是一瞬间的 Docker 视图，不能发现普通宿主机进程，也不能防止检查后新容器启动。实际备份/恢复和版本回退仍需麒麟现场停机维护窗口与真实制品演练，QA-033/034 保持未完成。
