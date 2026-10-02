# 旧 8080 document-intelligence 环境清理

日期：2026-10-02。用户明确要求停止并删除 8080 旧系统的服务、镜像、源码和绑定的持久化数据；当前 MinerU fork 的 8088 预览不在范围内。

删除前只读核对：三个运行容器均属于 Compose 项目 `document-intelligence`，配置文件在 `/Users/ahaha/Documents/longge/document-intelligence/docker/compose.production.yaml`；镜像分别为 `mineru:4.0.2`、`document-intelligence-backend:0.2.0`、`document-intelligence-frontend:0.2.0`，未见其他容器使用。两个数据卷均带同一 Compose 项目标签，未见其他容器挂载。旧源码目录是父级 `/Users/ahaha/Documents/longge` Git 仓库的一部分，内部有未提交修改，所以保留了可恢复的废纸篓副本。

执行结果：

- `docker compose ... -p document-intelligence down --volumes` 移除三个容器、`document-intelligence-network` 和 `document-intelligence_document-data`、`document-intelligence_mineru-data` 两个卷。Docker 卷数据已实际删除，不能从废纸篓恢复。
- `docker image rm` 移除上述三个精确镜像标签及对应镜像对象。
- 将 `/Users/ahaha/Documents/longge/document-intelligence` 完整移入 `/Users/ahaha/.Trash/document-intelligence-2026-10-02`；原位置不存在。源码和其中未提交改动仍可从废纸篓恢复。未删除父仓库其他文件，也未清理共享模型缓存或其他项目容器。
- 清理后核验：旧项目容器/镜像/数据卷未列出，8080 无连接；当前 fork 的 8088 返回 HTTP 200，未受影响。当前 MinerU fork 工作区保持干净。

本记录不代表当前 fork 的 Docker 镜像或麒麟部署已经完成；8088 仍为 Mac 本机源码预览。
