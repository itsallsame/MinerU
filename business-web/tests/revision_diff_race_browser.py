"""An old comparison failure must not appear under a newly selected comparison."""

from __future__ import annotations

import json
import sys
import time

from playwright.sync_api import sync_playwright


def main(base_url: str) -> None:
    now = int(time.time() * 1000)
    document = {"id": "a" * 32, "original_name": "notice.pdf", "sha256": "b" * 64,
                "size": 100, "created_at_ms": now, "template_code": None, "template_version": None}
    task = {"id": "c" * 32, "document_id": document["id"], "requested_tier": "basic",
            "actual_tier": "basic", "status": "done", "error_code": None,
            "created_at_ms": now, "updated_at_ms": now}
    revisions = [
        {"id": f"rev-{index}", "document_id": document["id"], "short_id": document["sha256"][:7],
         "tier": "basic", "page_range": "1", "producer_version": "4.0.6", "model_ref": None,
         "created_at_ms": now - index * 1000}
        for index in range(1, 4)
    ]

    def respond(route: object, body: object, status: int = 200) -> None:
        route.fulfill(status=status, content_type="application/json", body=json.dumps(body))

    def api(route: object) -> None:
        path = route.request.url.split("/api/business", 1)[1].split("?", 1)[0]
        if path == "/capabilities":
            respond(route, {"max_upload_bytes": 1000000, "parseable_extensions": ["pdf"],
                            "tiered_extensions": ["pdf"], "flash_only_extensions": [],
                            "tiers": ["flash", "basic", "standard", "advanced"]})
        elif path == "/templates":
            respond(route, [])
        elif path == "/documents":
            respond(route, {"items": [{"document": document, "task": task}], "total": 1, "limit": 20, "offset": 0})
        elif path == f"/documents/{document['id']}/revisions":
            respond(route, revisions)
        elif path in ("/revisions/rev-1/extractions", "/revisions/rev-1/evidence"):
            respond(route, [])
        elif path == "/revisions/rev-1/diff":
            respond(route, {"items": [{"page_no": 1, "status": "changed",
                                      "left_locator": "left", "right_locator": "right"}],
                            "next_page": None, "scanned_pages": 1})
        elif path in ("/revisions/rev-1/content", "/revisions/rev-2/content"):
            respond(route, {"content": "旧版页面", "truncated": False, "locator": "left", "next_locator": None})
        elif path == f"/documents/{document['id']}/source":
            route.fulfill(status=200, content_type="application/pdf", body=b"%PDF-1.4\n")
        else:
            raise AssertionError(f"Unexpected API path: {path}")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.add_init_script("""
                const originalFetch = window.fetch.bind(window);
                window.fetch = (input, options = {}) => {
                  if (window.__delayFragment && String(input).includes(window.__delayFragment)) {
                    window.__delayFragment = null;
                    window.__pendingFailure = true;
                    return new Promise((resolve) => {
                      window.__releaseFailure = () => resolve(new Response(
                        JSON.stringify({ detail: 'old_comparison_unavailable' }),
                        { status: 503, headers: { 'Content-Type': 'application/json' } }
                      ));
                    });
                  }
                  return originalFetch(input, options);
                };
            """)
            page.route("**/api/business/**", api)
            page.goto(base_url, wait_until="networkidle")
            page.get_by_role("button", name="查看 notice.pdf，已解析").click()
            comparison = page.locator(".review-section").filter(
                has=page.get_by_role("heading", name="解析修订逐页差异"))
            selector = comparison.get_by_label("对比的解析修订")
            selector.select_option("rev-2")
            page.evaluate("window.__delayFragment = '/revisions/rev-1/diff?'")
            comparison.get_by_role("button", name="比较修订").click()
            page.wait_for_function("window.__pendingFailure === true")
            selector.select_option("rev-3")
            page.evaluate("window.__releaseFailure()")
            page.wait_for_timeout(100)
            assert comparison.get_by_text("old_comparison_unavailable").count() == 0
            comparison.get_by_role("button", name="比较修订").click()
            comparison.get_by_text("第 1 页 · 页文本不同").wait_for()

            selector.select_option("rev-2")
            comparison.get_by_role("button", name="比较修订").click()
            comparison.get_by_text("第 1 页 · 页文本不同").wait_for()
            page.evaluate("window.__pendingFailure = false; window.__delayFragment = '/revisions/rev-2/content?'")
            comparison.get_by_role("button", name="读取两版此页").click()
            page.wait_for_function("window.__pendingFailure === true")
            selector.select_option("rev-3")
            page.evaluate("window.__releaseFailure()")
            page.wait_for_timeout(100)
            assert comparison.get_by_text("old_comparison_unavailable").count() == 0
            assert comparison.get_by_text("旧版页面").count() == 0
            page.evaluate("window.__pendingFailure = false; window.__delayFragment = '/revisions/rev-1/diff?'")
            comparison.get_by_role("button", name="比较修订").click()
            page.wait_for_function("window.__pendingFailure === true")
            page.evaluate("window.__releaseFailure()")
            page.get_by_text("old_comparison_unavailable").wait_for()
            comparison.get_by_role("button", name="比较修订").click()
            comparison.get_by_text("第 1 页 · 页文本不同").wait_for()
            assert page.get_by_text("old_comparison_unavailable").count() == 0
            assert not errors, errors
            print("Playwright revision comparison races passed: stale errors discarded, current errors recover")
        finally:
            browser.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18088")
