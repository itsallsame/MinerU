# 显式请求首次入库不额外排默认 Flash

日期：2026-09-24。源码基线：fork `master` 的 `9bc125b6`。环境：Mac、Python 3.13.5、临时 Doclib SQLite 数据库。此项是消费者感知取消协议的前置队列分流，不是 FE-016 完成证明。

首次 `request_parse` 过去调用 `refresh_file(... ensure_ingested=True)`，而 `ingest_file` 无条件排默认 Flash；随后 `request_parse` 再处理显式 tier。现在内部入库链新增 `queue_initial_parse` 开关：显式请求只做文件身份、元数据和 FTS 文件名入库，后续由请求自身选择/创建批次；后台主动 `ingest_file` 默认仍排 Flash。新测试对新 PDF 的 Standard 显式请求断言只有一个 Standard pending 行，并验证另一份后台入库文件仍创建 Flash 行。既有非 PDF 整本请求测试按真实新语义校正为“首次创建”而非“先自动创建后复用”。

边界：这个开关只区分当前同步调用链；后台 worker 同时入库可能抢先排系统批次。当前 `request_parse` 的多步读写尚未成为一个注册消费者的原子事务，不能据此取消共享批次。未改用户/权限管理、模型或离线部署工况。

验证：Doclib 缓存语义测试 `211 passed`；Mac 全量业务测试 `246 passed, 1 skipped`；MinerU 官方全量单元测试 `2806 passed, 4 skipped`。源码 Ruff lint、format 和 `git diff --check` 通过。曾在普通沙箱权限下运行 Doclib 测试，Unix socket 用例因本机权限被阻断；授权重跑后上述 211 项通过。未在隔离麒麟/NVIDIA 环境或真实四类标注样本上验证，也未宣称任务取消已实现。
