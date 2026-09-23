# 自有 Skill → 业务 Web 冻结证据链接

日期：2026-09-23。源码起点：`a432bfd8`。环境：Mac、Python 3.13/3.14、Playwright Chromium；目标麒麟/NVIDIA 未验证。

## 契约

- 自有 Skill 的 `evidence ID` 命令仍只读取 `/api/business/evidence/{id}`，在返回对象中补充 `web_url`：当前已校验的业务 Web/API 同源地址加 `/#evidence=ID`。不泄漏 Doclib 文件路径或 parse ID，也不构造官方 Skill 链接。
- Web 打开链接后先读取业务证据、业务文档及修订列表，选择证据所属修订，再显示冻结片段与当前定位状态。ID 不存在、修订不属于文档或证据不属于修订时显示错误；打开链接不创建字段决定、证据或成果。
- `changed` / `unavailable` 仍可查看冻结片段，但不提供“可靠跳转原文页”的按钮。无模板文档的证据面板不再被字段提取流程的提前返回隐藏。系统依然开放，无用户、角色或权限管理；链接可被任何能到达该业务服务的人使用。

## 验证

- `.venv/bin/python -m pytest -q tests/business/test_skill_contract.py`：6 项通过，2 个依赖弃用警告；覆盖 Skill 返回同源链接且只访问业务 API。
- `.venv/bin/python -m pytest -q tests/business`：99 项通过，2 个依赖弃用警告。
- `pnpm --dir business-web test`：7 项通过；`pnpm --dir business-web build`：7 个离线资源；Skill `quick_validate.py` 与 Ruff 通过。
- Playwright `business-web/tests/evidence_link_browser.py`：真实浏览器打开真实静态包，业务 API 使用合成记录拦截；验证无模板文档的正确文档/修订、冻结片段、`changed` 状态不提供原文页跳转、无写请求。测试静态服务随后停止。

## 未覆盖

- 浏览器测试的业务记录是合成数据；还需真实业务数据、真实长文档和目标麒麟离线环境端到端验收。
- 证据链接只指向业务 Web，不提供跨部署永久地址保证；换域名/端口后需用新地址重新生成链接。
