"""Playwright quality dashboard: real offline bundle, controlled business API responses."""

from __future__ import annotations

import json
import sys

from playwright.sync_api import sync_playwright


def main(base_url: str) -> None:
    calls: list[str] = []
    delayed: list[object] = []
    stats = {
        "documents": 2, "parse_pending": 1, "parse_failed": 0, "parse_done": 2,
        "revisions": 3, "extraction_pending": 0, "extraction_failed": 1, "extraction_done": 2,
        "open_issues": 1, "confirmed_runs": 1, "result_versions": 2,
    }

    def route_api(route: object) -> None:
        path = route.request.url.split("/api/business", 1)[1].split("?", 1)[0]
        calls.append(path)
        if path == "/capabilities":
            payload = {"max_upload_bytes": 1000000, "parseable_extensions": ["pdf"],
                       "tiered_extensions": ["pdf"], "flash_only_extensions": [],
                       "tiers": ["flash", "basic", "standard", "advanced"]}
        elif path == "/templates":
            payload = []
        elif path == "/documents":
            payload = {"items": [], "total": 0, "limit": 20, "offset": 0}
        elif path == "/quality-stats":
            if calls.count("/quality-stats") == 2:
                delayed.append(route)
                return
            payload = {**stats, "documents": 9} if calls.count("/quality-stats") == 3 else stats
        else:
            raise AssertionError(f"Unexpected API path: {path}")
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/business/**", route_api)
            page.goto(base_url, wait_until="networkidle")
            assert "/quality-stats" not in calls
            page.locator("#quality-panel > summary").click()
            page.locator("#quality-content .quality-metric").first.wait_for()
            assert page.locator("#quality-content .quality-metric").count() == 11
            assert page.locator("#quality-content .quality-metric").last.locator("span").inner_text() == "成果版本"
            assert page.locator("#quality-content .quality-metric").last.locator("strong").inner_text() == "2"
            assert page.get_by_text("统计不代表字段准确率或人工评估结果", exact=False).count() == 1
            page.get_by_role("button", name="更新统计").click()
            assert calls.count("/quality-stats") == 2
            page.get_by_role("button", name="更新统计").click()
            page.locator("#quality-content .quality-metric").first.locator("strong").get_by_text("9").wait_for()
            delayed[0].fulfill(status=200, content_type="application/json", body=json.dumps(stats))
            page.wait_for_timeout(100)
            assert page.locator("#quality-content .quality-metric").first.locator("strong").inner_text() == "9"
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            assert not errors, errors
            print("Playwright quality statistics passed: lazy load, refresh race, narrow viewport")
        finally:
            browser.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18088")
