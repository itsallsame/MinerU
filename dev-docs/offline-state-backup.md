# 隔离环境业务状态备份与恢复草案

适用范围：麒麟 x86_64 + NVIDIA 物理隔离部署的**业务 SQLite、Doclib 持久目录、共享原件目录**。业务系统保持开放，不设计账号、登录、角色或权限管理；下文“受控”只指部署网络、宿主文件与离线介质的运维边界。模型权重、模型清单、代码镜像和 wheelhouse 属于独立发布制品，不复制进业务状态备份；但备份附带当时的 `release.json` 字节与 SHA-256，回退时必须找回匹配的发布制品。备份包含原文和业务数据，应留在受控介质，不能提交 Git。当前仅有 Mac 合成演练，不能把它当成目标机验收。

## 停机备份

先选定与当前部署完全相同的 Compose 文件、环境文件和三个宿主挂载目录。在受控维护窗口停 `business-api` 与 `doclib-worker`，确认没有其他进程写这三个目录。工具会再次读取所选 Compose 项目的服务状态，并扫描本机**所有运行中 Docker 容器**的挂载：若有可写 bind/volume 挂载与任一所选状态目录重叠（包括父目录挂载），直接拒绝；Docker 不可用、容器清单/挂载无法完整读取或所选服务仍在运行也拒绝。该检查只是一个时间点，无法阻止随后新容器启动，也不能发现宿主机普通进程写入；维护窗口仍须现场核实。

```bash
docker compose --file docker/compose.business.yaml --env-file /受控部署目录/business.env stop business-api doclib-worker
.venv/bin/python -m scripts.business_state_backup create \
  --compose-file docker/compose.business.yaml --env-file /受控部署目录/business.env \
  --business-dir /业务数据宿主目录 --doclib-dir /Doclib数据宿主目录 \
  --shared-documents-dir /共享原件宿主目录 \
  --release-manifest /受控发布目录/release.json \
  --output /受控备份目录/本次唯一备份名
.venv/bin/python -m scripts.business_state_backup verify --backup /受控备份目录/本次唯一备份名
```

`create` 只接受**不存在**的输出目录，并要求输入/输出目录互不重叠。它拒绝符号链接、硬链接、特殊文件、旧业务 schema 和 SQLite 完整性错误；复制后三组数据逐文件核对大小与 SHA-256，最后才写入 `COMPLETE` 标记。失败不会删除旧数据，也不会把未完成输出伪装成可恢复备份；若留下无 `COMPLETE` 的新目录，请保留排障，另选名称重做。校验清单不是数字签名，不能替代受控介质、访问控制和现场交接记录。

## 恢复及版本回退

恢复只写入**三个全新的、此前不存在的目标目录**，不覆盖现有挂载数据；目标目录的父目录须已存在。先执行 `verify`，并找回与备份匹配的原发布清单。`restore` 会强制逐字节核对所选 `release.json` 的 SHA-256，在不匹配时写入任何新目录之前拒绝；复制前还检查所有运行容器是否可写挂载新目标目录或备份目录；之后仍须在停服状态运行：

```bash
.venv/bin/python -m scripts.business_state_backup restore \
  --compose-file docker/compose.business.yaml --env-file /受控部署目录/business.env \
  --backup /受控备份目录/本次唯一备份名 \
  --release-manifest /匹配的原发布目录/release.json \
  --business-dir /新业务数据目录 --doclib-dir /新Doclib数据目录 \
  --shared-documents-dir /新共享原件目录
```

若恢复中断，可能留下部分新目录；工具不会自动删除或重用它们，应检查后改用新的目录名。恢复后按容器运行 UID/GID 核对目录所有权、权限和 SELinux/麒麟安全策略，再把三个 Compose 宿主挂载路径切到新目录。仅与该备份的源码、镜像、模型和依赖兼容时才能启动；运行发布制品校验及 `scripts.verify_business_runtime`，再用实际文档核对原件、解析修订、冻结证据和已确认成果。模型权重不随备份恢复，不能只换数据库或只换镜像就宣称版本回退完成。

业务数据库目前只接受 schema 13；旧 schema 12 及更早版本不迁移。当前备份脚本也只验证 schema 13，不能用于把旧库转换成新库。QA-033 数据备份恢复和 QA-034 版本回退仍须在目标隔离麒麟机器上以实际制品、模型、GPU 和受控样本完成演练后才能标通过。
