# 原文格式分流与解析修订读取

日期：2026-09-23。范围：P4 FE-020/021/022/024/025 的浏览器与 API 契约核验，分别按特性清单记录完成状态。

- PDF：同源 HEAD 完整性核验成功后显示内嵌 PDF；浏览器测试向预览请求提供仓库已有的有效 17 页 `demo1.pdf`，验证页 1 初始 URL 和显式切换到页 2 的 URL。页级输入不是对 PDF 查看器内部滚动位置的实时监听。
- 图片：HEAD 成功后浏览器读取 1×1 PNG，验证 `naturalWidth=1`、原图替代文本与单页关联字段入口；图片加载失败撤下预览的旧回归仍保留。
- Office：DOCX 不显示 PDF/图片预览或 PDF 页码，明确提示浏览器不支持原文预览；显示带文件名的同页下载链接。后端 TestClient 验证 DOCX 响应为 `attachment`、PDF/PNG 为 `inline`，并保留 `nosniff`。Playwright 模拟 API 的下载导航不被其请求拦截可靠覆盖，因此没有声称浏览器端实际下载文件已验收。
- 历史解析：在复核工作台切到旧修订，读取旧版页 Markdown；切回新修订后旧文本清空，再读取新版页 Markdown。文本显示为机器解析原文，未人工确认，也不是冻结证据。后端历史读取按修订绑定的测试通过。

验证：`cd business-web && pnpm test && pnpm build` 为 11 个单测通过、8 个离线资源；Playwright `source_formats_browser.py` 与 `review_browser.py` 通过；`.venv/bin/python -m pytest tests/business/test_business_api.py::test_open_document_library_capabilities_and_source -q` 为 1 passed，`tests/business/test_business_discovery.py -q` 为 12 passed（后端测试各有 2 个已有依赖弃用警告）。

边界：浏览器使用模拟业务 API；没有证明真实 Office 文件能在浏览器内预览、PDF 文字选择/bbox 像素精度或四类真实业务样本质量。麒麟 x86_64/NVIDIA 完全离线目标机验收仍待完成。系统不增加用户或权限层。
