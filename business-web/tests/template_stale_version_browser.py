"""A stale template editor cannot silently append or disable after another update."""

from __future__ import annotations

import json
import sys
import time

from playwright.sync_api import sync_playwright


def main(base_url: str) -> None:
    now = int(time.time() * 1000)
    template = {
        "code": "my_report", "name": "初版", "version": 1, "built_in": False, "enabled": True,
        "fields": [{"code": "title", "label": "标题", "type": "text", "required": False}],
        "created_at_ms": now,
    }
    writes: list[tuple[str, int]] = []
    disables: list[tuple[str, int]] = []

    def fulfill(route: object, payload: object, status: int = 200) -> None:
        route.fulfill(status=status, content_type="application/json", body=json.dumps(payload, ensure_ascii=False))

    def api(route: object) -> None:
        nonlocal template
        request = route.request
        path = request.url.split("/api/business", 1)[1].split("?", 1)[0]
        if path == "/capabilities":
            fulfill(route, {
                "max_upload_bytes": 1000000, "parseable_extensions": ["pdf"],
                "tiered_extensions": ["pdf"], "flash_only_extensions": [],
                "tiers": ["flash", "basic", "standard", "advanced"],
            })
        elif path == "/documents" and request.method == "GET":
            fulfill(route, {"items": [], "total": 0, "limit": 20, "offset": 0})
        elif path == "/templates" and request.method == "GET":
            fulfill(route, [template])
        elif path == "/templates/my_report" and request.method == "PUT":
            expected = request.headers.get("if-match", "")
            writes.append((expected, template["version"]))
            if expected != f'"{template["version"]}"':
                fulfill(route, {"detail": "Template version changed; reload before editing"}, 409)
            else:
                template = {**template, **json.loads(request.post_data), "version": template["version"] + 1}
                fulfill(route, template)
        elif path == "/templates/my_report/disable" and request.method == "POST":
            expected = request.headers.get("if-match", "")
            disables.append((expected, template["version"]))
            if expected != f'"{template["version"]}"':
                fulfill(route, {"detail": "Template version changed; reload before editing"}, 409)
            else:
                template = {**template, "enabled": False}
                fulfill(route, template)
        else:
            raise AssertionError(f"Unexpected API call: {request.method} {path}")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            first = browser.new_page()
            stale = browser.new_page()
            stale_disable = browser.new_page()
            errors: list[str] = []
            stale_disable.on("dialog", lambda dialog: dialog.accept())
            for page in (first, stale, stale_disable):
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.route("**/api/business/**", api)
                page.goto(base_url, wait_until="networkidle")
                page.locator("#template-manager > summary").click()
                page.get_by_role("button", name="初版 · v1").click()

            first.locator('.template-editor [name="template-name"]').fill("先保存")
            first.get_by_role("button", name="保存新版本").click()
            first.get_by_text("先保存 第 2 版已保存。").wait_for()
            assert template["version"] == 2

            stale.locator('.template-editor [name="template-name"]').fill("过期草稿")
            stale.get_by_role("button", name="保存新版本").click()
            stale.get_by_role("alert").get_by_text("模板版本已被其他操作更新", exact=False).wait_for()
            assert stale.locator('.template-editor [name="template-name"]').input_value() == "过期草稿"
            assert stale.get_by_role("button", name="保存新版本").is_disabled()
            assert writes == [('"1"', 1), ('"1"', 2)] and template["version"] == 2
            stale_disable.get_by_role("button", name="停用此自定义模板").click()
            stale_disable.get_by_role("alert").get_by_text("模板版本已被其他操作更新", exact=False).wait_for()
            assert disables == [('"1"', 2)] and template["enabled"]
            assert not errors, errors
            print("Playwright template stale-version guard passed: old editor cannot append or disable")
        finally:
            browser.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18088")
