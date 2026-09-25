# Web 与自有 Skill 手动提取请求键接入（2026-09-25）

## 契约与行为

上一阶段后端已经把手动提取的 `Idempotency-Key` 与修订、运行同事务持久绑定。此前 Web/Skill 未传此键；未知 POST 后只能列出修订运行，不能确定哪一项属于自己的请求。

Web 现在在 POST 前生成随机键，把 `{revisionId, requestKey}` 写进同源浏览器 `localStorage`；若无法写入，则不发送请求。POST 响应丢失、408/5xx 或成功响应损坏时，仅 GET `/api/business/extraction-requests/{key}`：返回匹配运行即可归因并清除本地待决项；404 或读回失败则保留键，不断言写入失败。刷新页面、重开同一浏览器资料后仍能按原键核对；明确确认后才允许用**原键**重试，不会自动生成新键重发。确定的业务 4xx 清除待决项。存储只含随机键和业务修订 ID，不含原文或用户身份；共享浏览器资料的本地存储并非权限隔离措施。

自有 `business-documents` Skill 的 `extract` 命令同样传键，在成功 JSON 中返回键，在不确定错误中附键；结果未知时只读按键查询。`extraction-request KEY` 可在后续调用中只读核对；404 为 `not_recorded_at_lookup`，不证明拒绝。用户再次明确要求重试同一提取时，调用方可用 `extract REVISION_ID --request-key KEY --confirm-write`，服务端重放原运行。Skill 本身不建立用户或权限层，也不在本地秘密文件中持久保存键；跨 Agent 会话留存由调用方负责。

## Mac 验证与边界

- Web 单测 13 通过，离线构建 8 个资源，完整 Chromium 浏览器回归 26 项通过。扩展的浏览器用例覆盖 503 后按键 GET 503、页面重载后同键 GET 成功、未知写入时不额外 POST、按键 GET 404 后重载并显式同键 POST 重试。其余 Web 回归仍通过。
- Skill 定向契约 17 通过，涵盖请求头、响应/错误键、按键只读找回、404、409 和不可用查询。服务端持久键与并发行为另见 `2026-09-25-extraction-request-idempotency.md`。
- 完整业务测试第一次在受限沙箱中因禁止绑定本机 Doclib Unix socket/端口而集中报环境错误；使用允许本机测试服务的权限重跑后为 `373 passed, 2 skipped, 2 dependency warnings`。这不是功能失败，但也不是目标生产机验证。

这些是 Mac 合成网络响应与浏览器行为验证，不等于真实断网或隔离麒麟 x86_64＋NVIDIA 运行验收；四类脱敏原文与人工标注也尚不可用。Web 对其他写操作的跨会话恢复仍不完整；本阶段只解决手动提取请求归因与同键重试。
