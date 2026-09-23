# 历史原生块摘要的业务边界验证（Mac）

日期：2026-09-23。范围：MinerU 4.0.6 fork 的 Doclib 公共接口、开放业务 API、Web、自有 Skill。

## 实现与限制

- Doclib `GET /parses/{parse_id}/structure?page_no=...` 只读取指定已完成或保留的 superseded 批次的持久解析结果。每页顶层块返回类型、索引/块定位器、可用标题级别及 bbox；文本类块预览至多 200 字符，不序列化图像 payload、资源路径或超长正文。索引缺失时只给页定位器。
- 业务 API `GET /api/business/revisions/{id}/structure?page_no=...` 从业务修订映射历史 parse ID，核验源身份、页码与定位器；响应不暴露 parse ID 和内部文件路径。Web 可按页查看块并读取块定位文本；Skill `structure` 使用同一业务 API。
- 这些是机器解析的**顶层块摘要**，不等于人工确认的证据，也尚未展开表格单元格、列表项或图像内部层级。FE-023 完整解析结构树仍未完成，FE-018 块级检索证据列表也未完成。

## 验证

- 真实 Doclib HTML H1 的原生块包含对应标题预览；强制重解析后人为变更新批次，旧 parse ID 的块预览仍为旧标题，新 parse ID 显示新标题。
- 仓库 13 页 PDF 经真实业务 API 读取第 13 页原生块，验证跨批次页映射和公开响应无 parse ID。
- Mock 契约测试验证业务页到批次选择、定位器/身份校验和非法页拒绝；Skill 通过真实 FastAPI ASGI 客户端调用该接口。
- 全量业务测试 111 项通过（2 条依赖弃用警告）；Doclib 接口、路由和渐进读取相关上游测试 41 项通过；Web 测试 9 项/离线构建 8 资产，Playwright 原生块显示与既有复核流程通过；Skill 快速校验、Ruff 和 diff 检查通过。

## 目标环境

以上均在 Mac 开发环境验证。物理隔离的麒麟 x86_64 + NVIDIA、模型挂载、GPU 档位质量和四类人工标注业务样本尚未验收，不能据此声称生产可用。
