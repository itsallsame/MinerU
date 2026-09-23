# 业务范围检索与历史解析渐进读取

日期：2026-09-23。源码起点：`dfb6874f`。环境：Mac Apple Silicon、Python 3.13/3.14、Node 24、Playwright Chromium；目标麒麟/NVIDIA 未运行。

## 契约与边界

- `GET /api/business/search?query=...&limit=...` 使用当前 Doclib 索引，但只映射到业务库中已有**完成的同 SHA-256、同 tier 解析修订**的文档；同内容的不同业务文档分别列出。返回业务 ID、最近的匹配修订、文档级片段和 `current_index_unconfirmed`，省略 Doclib 文件路径。业务侧最多扫描 500 个 Doclib 命中；`scan_complete` 只表示本次**Doclib 报告的结果窗口**是否读尽，并不证明全库穷尽或精确业务命中总数。当前上游 FTS 自身也有候选窗口上限，需在目标数据规模下验证召回。
- Doclib `SearchResult` 不含页/块定位器，搜索片段只是当前索引预览，不能当作冻结证据、精确引用或已确认成果。当前还没有搜索结果页块证据列表。
- `GET /api/business/revisions/{id}/content?locator=...&limit=...` 使用业务修订保存的历史 parse ID 调用 Doclib 公共 `read_parse_content`；核对 locator 短 ID/tier、返回 SHA-256/短 ID/tier/请求定位器，拒绝跨文档与跨 tier 读取。返回最多 30,000 字符的机器解析内容、`next_locator`、`historical_parse_unconfirmed`，不返回 Doclib parse ID 或 asset 路径。历史批次被清理时失败，不回退到当前批次。
- Web 展示检索结果和历史解析页/下一段，Skill 增加 `search` 与 `read` 命令，均通过同一业务 API。读取不自动冻结证据，也不自动确认字段；无登录、用户或权限管理。

## 验证

- `.venv/bin/python -m pytest -q tests/business/test_business_discovery.py tests/business/test_skill_contract.py`：9 项通过，2 个依赖弃用警告。覆盖非业务 Doclib 命中过滤、同 SHA 不同业务文档、路径/parse ID 不外泄、历史 parse ID、next locator、身份漂移、无服务、无效定位器/限额和 Skill 调用真实 FastAPI 业务契约。
- `.venv/bin/python -m pytest -q tests/business/test_doclib_contract.py::test_business_discovery_uses_real_local_doclib_without_exposing_paths`：1 项通过；独立本机 Doclib 进程解析合成 HTML，检索业务文档并按业务修订读取历史内容。仅验证本地 Flash/CPU 路径，不代表 GPU 或复杂 PDF 性能。
- `.venv/bin/python -m pytest -q tests/business`：98 项通过、2 个依赖弃用警告；`pnpm --dir business-web test`：7 项通过；`pnpm --dir business-web build`：7 个离线资源。Ruff 与 `git diff --check` 通过。
- Playwright：`business-web/tests/discovery_browser.py` 走真实静态页面 + 合成业务 API 记录，完成检索、选择文档、历史页读取与下一定位器续读，并验证 390px 窄屏无横向溢出；已有 `browser_smoke.py` 和 `review_browser.py` 回归通过。截图：[检索与续读](screenshots/p4-discovery-desktop.png)。

## 尚未验收

- Playwright 的文档/检索返回被拦截，不能代替“实际浏览器 + 真实 Doclib + 真实业务数据”的全链路；真实 Doclib 用例仅覆盖合成原生 HTML，不含四类业务脱敏样本、扫描/表格、麒麟 GPU。
- 搜索结果无页块定位器；后续若需要“点击命中直达证据”，须先建立明确的业务证据映射，不能从文档级 snippet 伪造坐标。业务侧 500 命中扫描上限、Doclib 当前 FTS 候选窗口和文档去重策略都可能遗漏更靠后的业务结果，需要现场数据规模和索引策略验收。
- 当前 Web 显示机器解析 Markdown 原文而非结构树；结构化差异、字段高亮、模板/运营页面、Skill 深链与真实长文档渐进读取仍待完成。无身份权限的开放系统只能由隔离区网络边界限制可达性。
