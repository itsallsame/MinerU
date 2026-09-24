# FE-046 增量：键盘跳转主内容的真实焦点

Mac Chromium 回归发现：“跳转到主要内容”链接原先把 URL 更新为 `#main`，却没有把 DOM 焦点移到主内容区。键盘用户仍留在链接上，后续 Tab 不从主内容开始。给 `<main id="main">` 添加 `tabindex="-1"` 后，浏览器可将片段跳转的焦点落到主内容，且该焦点有可见轮廓；再次 Tab 到主区域内的“刷新列表”按钮。文档列表键盘选中并重绘后的焦点保持原有测试继续通过。

验证：

- 修复前新增断言使 `pnpm test:browser` 在 `keyboard_focus_browser.py` 明确失败：`skip link did not move focus to main`。
- 修复后 `cd business-web && pnpm test && pnpm build && pnpm test:browser`：11 个 Node 单测、8 个离线资产、19 个 Chromium 场景通过。
- 补充焦点轮廓和下一次 Tab 顺序断言后再次运行 `pnpm test:browser`：19 个场景通过。
- `git diff --check` 通过。

这只覆盖 Mac Chromium 下的跳转链接、主区域焦点与既有关键操作。尚未完成全页面逐项键盘遍历和真实屏幕阅读器朗读检查，`FE-046` 保持未完成；不声称麒麟浏览器现场通过。开放系统无用户或权限层。
