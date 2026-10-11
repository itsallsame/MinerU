# Mac 本地真实混合格式批量解析

日期：2026-10-11。环境：Mac arm64，本 fork `master` 基线 `5c19a435c5ef554932895325073c81ebe4f754c7` 加本轮 Web 解析结果展示改动；业务 Web/API `127.0.0.1:8088`、Doclib `127.0.0.1:15980`、本地 managed Standard parse-server。模型为本机 Torch 小模型与 llama.cpp VLM，Doclib 启动时设置 `MINERU_DOCLIB_REMOTE_DISABLED=1`、`MINERU_MODEL_SOURCE=local`；检查状态时本地 parse-server 健康，远程 URL 为空。

本轮复用 `/private/tmp/mineru-8088-preview` 的业务、上传和 Doclib 持久目录，在 Playwright 浏览器中**一次选择并提交 3 个真实文件**，没有模拟 API 响应。测试开始前 8088 已停止；重启时保留原业务库与四份旧文档，没有清理数据。原文件仍在 `~/Downloads`，未复制进 Git。

| 输入 | SHA-256 | 请求/实际档位 | 终态 | 页面解析结果 |
| --- | --- | --- | --- | --- |
| `Canal 及canal.admin-v1.1.5-踩坑记录.pdf`（839488 B） | `d0dc210532a6bccb46779ccee06be63e9a6b9623690a7be2e631ff92866553bb` | Standard / Standard | `done`，无错误码 | 第 1 页可见，首次读取 8377 字符；点击“继续读取后续内容”后累计 8457 字符，无错误 |
| `P020250623585737277218.docx`（13840 B） | `77489aff15838f1b77aacabaf75059bd351a66aa86a858f99752fc8dc8c8bd06` | 不显式指定 / Flash | `done`，无错误码 | 第 1 页可见，1044 字符，无错误 |
| `open-06.png`（343416 B） | `3e5f4e03295e6d555fa2c2adf83014deba66b81dec5d2f054aa223c2977e2c91` | Standard / Standard | `done`，无错误码 | 第 1 页可见，849 字符，无错误 |

核验路径：浏览器上传反馈显示三个文件各自“已受理”；业务文档列表最终均为“已解析”；逐份选中后，页面“解析结果”区域自动显示对应修订及正文；业务 API 的 `GET /documents`、`GET /documents/{id}/revisions`、`GET /revisions/{id}/content` 返回的档位、状态与页面一致。DOCX 归一为 Flash 符合当前格式规则。PDF 的源元数据日期格式异常曾在解析日志产生 warning，但任务仍完成；本次不将 warning 误报为解析失败。

结论：Mac 本机“混合格式多文件一次上传 → 各自路由档位 → 完成解析 → 页面阅读/长内容续读”流程通过。**不评价正文准确率或视觉版面质量**；未验证麒麟 x86_64＋NVIDIA/vLLM、物理断网容器、并发压力、失败文件隔离、四类人工标注样本及生产验收。本记录不能代替正式发布清单对源码 SHA 与制品的绑定证明。
