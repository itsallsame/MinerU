# 自有 Skill 的取消与再提取未知结果（2026-09-25）

## 发现

自有 Skill 的 `retry` 已把断线、HTTP 408/5xx 和不可读/不完整的成功响应当作未知写入结果，只读查询原任务；`cancel` 和手动 `extract` 原先仅抛出通用异常。Agent 若据此再次执行命令，可能重复取消请求或额外创建一次字段提取运行。后一条 API 目前没有写入幂等键，读取运行列表也无法把某个运行精确归因于本次 POST。

## 契约

- 四条 Skill 写命令仍需针对明确文件、任务或修订的 `--confirm-write`；它不是登录或权限机制。
- `cancel` 和 `extract` 各最多发送一次 POST。断线、HTTP 408/5xx、不可读或身份不完整的成功响应，统一视为未知结果。明确的 4xx（408 除外）按业务冲突/错误报告。
- `cancel` 未知时只读查询同一任务，返回 `cancel_outcome_unconfirmed` 和观察到的任务状态；该状态不证明这一次 POST 成功。
- `extract` 未知时只读列出同一修订的提取运行，返回 `extraction_outcome_unconfirmed`。包括空列表在内的观察结果均不证明本次 POST 成功或失败；不得自动再提交。读回不可用或身份不匹配时保留明确的“结果未知”错误，让 Agent 后续再查。
- `retry` 使用同一未知写入判断和任务只读核对；已有上传请求键逻辑不变。Web/API 与 Skill 仍指向同一开放业务契约，不直连 Doclib。

## 验证

新负例先在 `cancel` 和 `extract` 的 503 响应下失败，证实旧脚本未只读核对。修正后 Skill 契约测试 16 passed，覆盖 503、成功响应缺字段、读回也失败、明确 409 不额外 GET，以及无第二次 POST；完整 Mac 业务套件 367 passed、2 skipped、2 dependency warnings。Ruff 和 diff 检查通过。

此处是 Mac 的契约/替身验证，不是隔离麒麟机器上的真实网络断线测试；四类真实标注样本和目标机入口仍不可用。
