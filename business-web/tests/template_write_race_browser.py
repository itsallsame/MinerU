"""Delayed template writes refresh metadata without changing a newer template selection."""

from __future__ import annotations

import json
import sys
import time

from playwright.sync_api import sync_playwright


def main(base_url: str) -> None:
    now = int(time.time() * 1000)
    builtin = {
        "code": "official_document", "name": "公文", "version": 1, "built_in": True,
        "enabled": True, "created_at_ms": now,
        "fields": [{"code": "title", "label": "标题", "type": "text", "required": True}],
    }
    custom = {
        "code": "my_report", "name": "我的报告", "version": 1, "built_in": False,
        "enabled": True, "created_at_ms": now,
        "fields": [{"code": "title", "label": "标题", "type": "text", "required": True}],
    }
    templates = [builtin, custom]
    writes: list[str] = []

    def api(route: object) -> None:
        request = route.request
        path = request.url.split("/api/business", 1)[1].split("?", 1)[0]
        if path == "/capabilities":
            payload = {
                "max_upload_bytes": 1000000, "parseable_extensions": ["pdf"],
                "tiered_extensions": ["pdf"], "flash_only_extensions": [],
                "tiers": ["flash", "basic", "standard", "advanced"],
            }
        elif path == "/documents":
            payload = {"items": [], "total": 0, "limit": 20, "offset": 0}
        elif path == "/templates" and request.method == "GET":
            payload = templates
        elif path == "/templates/my_report" and request.method == "PUT":
            writes.append("update")
            templates[1] = {**templates[1], **json.loads(request.post_data), "version": 2}
            payload = templates[1]
        elif path == "/templates/my_report/disable" and request.method == "POST":
            writes.append("disable")
            templates[1] = {**templates[1], "enabled": False}
            payload = templates[1]
        else:
            raise AssertionError(f"Unexpected business API call: {request.method} {path}")
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload, ensure_ascii=False))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("dialog", lambda dialog: dialog.accept())
            page.add_init_script("""
                const originalFetch = window.fetch.bind(window);
                window.__delayTemplate = (fragment) => {
                  window.__delayFragment = fragment;
                  window.__pendingTemplate = false;
                  window.__settledTemplate = false;
                };
                window.fetch = (input, options = {}) => {
                  if (window.__delayFragment && String(input).includes(window.__delayFragment)) {
                    window.__delayFragment = null;
                    window.__pendingTemplate = true;
                    return new Promise((resolve, reject) => {
                      window.__releaseTemplate = () => originalFetch(input, options).then((response) => {
                        window.__settledTemplate = true;
                        resolve(response);
                      }, reject);
                    });
                  }
                  return originalFetch(input, options);
                };
            """)
            page.route("**/api/business/**", api)
            page.goto(base_url, wait_until="networkidle")
            page.locator("#template-manager > summary").click()
            page.get_by_role("button", name="我的报告 · v1").click()
            page.locator('.template-editor [name="template-name"]').fill("新版报告")
            page.evaluate("window.__delayTemplate('/templates/my_report')")
            page.get_by_role("button", name="保存新版本").click()
            page.wait_for_function("window.__pendingTemplate === true")
            page.get_by_role("button", name="公文 · v1 · 内置").click()
            page.get_by_text("内置模板只读；需要不同字段时").wait_for()
            page.evaluate("window.__releaseTemplate()")
            page.wait_for_function("window.__settledTemplate === true")
            page.get_by_role("button", name="新版报告 · v2").wait_for()
            assert page.get_by_text("内置模板只读；需要不同字段时").count() == 1
            assert page.get_by_text("新版报告 第 2 版已保存。").count() == 0

            page.get_by_role("button", name="新版报告 · v2").click()
            page.evaluate("window.__delayTemplate('/templates/my_report/disable')")
            page.get_by_role("button", name="停用此自定义模板").click()
            page.wait_for_function("window.__pendingTemplate === true")
            page.get_by_role("button", name="公文 · v1 · 内置").click()
            page.evaluate("window.__releaseTemplate()")
            page.wait_for_function("window.__settledTemplate === true")
            page.get_by_role("button", name="新版报告 · v2 · 已停用").wait_for()
            assert page.get_by_text("内置模板只读；需要不同字段时").count() == 1
            assert page.get_by_text("新版报告 已停用；历史版本仍可读取。").count() == 0
            assert writes == ["update", "disable"]
            assert not errors, errors
            print("Playwright template races passed: delayed save and disable preserve newer selection")
        finally:
            browser.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18088")
