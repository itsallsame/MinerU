# FE-033 B：历史结构节点完整文字只读契约（第一开发关口）

日期：2026-09-29。环境：本 fork `master`、Mac、项目 `.venv`；本记录随代码提交。用户已选择结构编辑 B，本次仅补其必需的**历史节点完整文字读取**，不声称人工编辑、派生版本、浏览器或 Skill 写入已完成。

Doclib 增加 `read_parse_structure_node_text(parse_id, page_no, path)`；业务 API 对应 `GET /api/business/revisions/{revision_id}/structure/node-text?page_no=N&path=0.1`。调用者使用业务解析修订 ID，不能提供内部 parse ID；服务端只读该修订在该页所属的历史解析批次。响应返回原生节点路径、类型、顶层来源 locator、`editable` 和完整 `text`，标记为 `historical_parse_unconfirmed`。子节点仍只继承顶层来源锚点，不获得伪造的独立精确定位器。

只有纯文字 span 的文本类节点与代码正文可得到完整原文；列表/表格/图片、含行内公式或其他非纯文字内容的节点返回 `editable=false, text=null`，防止扁平化时丢语义。文本保留原有空白，不取现有 200 字 `preview`，超过 100,000 字明确拒绝而不截断。页、路径、节点数量及解析批次受限；业务层复核 Doclib 身份、路径、类型、顶层定位器和预览一致性，拒绝跨修订/路径不匹配。

验证：

- `tests/business/test_business_discovery.py`：12 passed，1 条第三方警告；覆盖业务路由、归属、错误路径、伪造块号拒绝和不暴露内部解析 ID。
- `tests/business/test_doclib_contract.py` 与上述文件的相关完整回归：41 passed、1 skipped、2 条第三方警告，约 81 秒。默认跳过外部 Downloads PDF。
- 公开 `~/Downloads/rmrb2023121903.pdf` 经真实本机 Doclib＋业务 API 的上传、解析、历史结构和首块节点文字只读链路：1 passed、2 条第三方警告。原文与全文没有提交 Git。
- 新增长正文与嵌套子节点契约：原生预览 200 字、节点全文超过 200 字且保留换行；列表父节点与混合公式节点不可作为纯文字编辑；无效路径/页/parse ID 拒绝；旧/新解析批次同一路径返回各自历史文本。相关测试在最终修订后单独重跑。
- Ruff `check` 和 `git diff --check` 通过。仓库现存文件并非整体 Ruff formatter 标准格式，`ruff format --check` 对原有大文件产生大量既有格式差异；本次不机械重排这些文件。

边界：本机 Flash/只读 API 测试不证明 OCR、模型四档、结构准确率、人工 B 写入或麒麟离线 GPU。结构编辑下一关口是业务侧稳定节点 ID、派生页版本、有限操作与乐观冲突检查；冻结证据/已确认成果不得自动改变。
