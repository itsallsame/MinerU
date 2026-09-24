# SKL-020：长文档渐进读取示例

自有 Skill 增加按业务文档修订渐进阅读的可执行命令示例：先用 `revisions` 取得真实修订 ID，在 `outline` 的 25 页窗口中根据 `next_page` 继续，围绕问题用 `search-blocks` 搜索并按其 `next_page` 续查，再使用服务返回的 locator 调用 `read`，仅在需要时按 `next_locator` 继续。全过程固定同一历史修订，不拼造 `short_id` 或定位器，不把未扫描窗口当成“无匹配”，也不把机器片段当成冻结证据或确认成果。

验证：`.venv/bin/python -m pytest tests/business/test_skill_contract.py` 为 8 通过；新增契约测试核对同一修订的 outline/search-blocks 分页、返回定位器读取、未确认状态，以及全部请求仅走同一 `/api/business` 的 GET。Ruff lint 和 `git diff --check` 通过。

这证明 Mac 上的 Skill 命令契约与流程示例，不证明真实几百页文档的解析性能或麒麟/NVIDIA 离线运行；真实样本仍需单独验收。
