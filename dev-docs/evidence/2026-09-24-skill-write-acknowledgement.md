# SKL-017：自有 Skill 写操作确认检查

自有 Skill 与前端共用开放业务 API。系统没有登录、角色、令牌或权限层，但 Agent 不应在只读问题中静默创建文档或提取任务。`upload` 和 `extract` 是当前 Skill CLI 唯二写操作；两者现在要求显式 `--confirm-write`，未携带时在任何业务 API 请求前失败。Skill 说明要求 Agent 先确认用户明确请求该文件上传或该修订的提取，并核对批准的隔离服务地址；该标志仅是 Agent 侧操作确认，**不是**服务端鉴权，也不能代替网络隔离。

验证（Mac）：

- `.venv/bin/python -m pytest tests/business/test_skill_contract.py`：7 通过；新增断言证明无确认参数时不调用上传或抽取 API，带参数时仍走同一业务客户端。
- `MINERU_RUN_LIVE_BROWSER=1 .venv/bin/python -m pytest -q tests/business/test_live_web_smoke.py --tb=short`：1 通过；真实本地 Web/API/Doclib/Skill 链路中的上传和提取都携带确认参数。
- `PYTHONPATH=. .venv/bin/pytest -q tests/business`：187 通过、1 个 opt-in 跳过、2 个依赖弃用警告。
- Ruff lint 与 `git diff --check` 通过。

`SKL-017` 标记通过，计数 71/241。仍未验证麒麟 x86_64/NVIDIA、完全断网、真实四类样本和 Agent 在所有自然语言场景中是否遵守确认说明。CLI 标志不会阻止其他调用者直接访问开放 API；隔离网络仍是部署边界。
