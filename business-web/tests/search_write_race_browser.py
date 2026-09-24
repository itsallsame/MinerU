"""Delayed search-result actions must not restore an obsolete document context."""

from __future__ import annotations

import json
import sys
import time

from playwright.sync_api import sync_playwright


def main(base_url: str) -> None:
    now = int(time.time() * 1000)
    documents = [
        {
            "id": char * 32,
            "original_name": name,
            "sha256": char * 64,
            "size": 16,
            "created_at_ms": now,
            "template_code": None,
            "template_version": None,
        }
        for char, name in (("a", "first.pdf"), ("b", "second.pdf"))
    ]
    tasks = [
        {
            "id": char * 32,
            "document_id": document["id"],
            "requested_tier": "flash",
            "actual_tier": "flash",
            "status": "done",
            "error_code": None,
            "created_at_ms": now,
            "updated_at_ms": now,
        }
        for char, document in (("c", documents[0]), ("d", documents[1]))
    ]
    calls: list[str] = []
    writes: list[str] = []
    locator = f"doc:{documents[0]['sha256'][:7]}/tier:flash/page:1/block:1"
    revisions_fail = False

    def api(route: object) -> None:
        path = route.request.url.split("/api/business", 1)[1].split("?", 1)[0]
        calls.append(path)
        if path == "/capabilities":
            payload = {
                "max_upload_bytes": 1000000,
                "parseable_extensions": ["pdf"],
                "tiered_extensions": ["pdf"],
                "flash_only_extensions": [],
                "tiers": ["flash", "basic", "standard", "advanced"],
            }
        elif path == "/templates":
            payload = []
        elif path == "/documents":
            payload = {
                "items": [{"document": document, "task": task} for document, task in zip(documents, tasks)],
                "total": 2,
                "limit": 20,
                "offset": 0,
            }
        elif path in (f"/documents/{document['id']}/revisions" for document in documents):
            if path == f"/documents/{documents[0]['id']}/revisions" and revisions_fail:
                route.fulfill(status=503, content_type="application/json", body='{"detail":"revisions_unavailable"}')
                return
            payload = []
        elif path.endswith("/source"):
            route.fulfill(status=200, content_type="application/pdf", body=b"%PDF-1.4\n")
            return
        elif path == "/search":
            payload = {
                "items": [{"document": documents[0], "revision_id": "rev-a", "tier": "flash", "snippet": "原文候选"}],
                "scan_complete": True,
                "scanned_doclib_hits": 1,
            }
        elif path == "/revisions/rev-a/search-blocks":
            payload = {
                "revision_id": "rev-a",
                "scanned_pages": 1,
                "next_page": None,
                "items": [{
                    "locator": locator,
                    "page_no": 1,
                    "block_no": 1,
                    "bbox": None,
                    "snippet": "原文候选",
                    "state": "historical_parse_unconfirmed",
                }],
            }
        elif path == "/revisions/rev-a/evidence":
            writes.append(path)
            payload = {"id": "evidence-a"}
        elif path == "/revisions/rev-a/content":
            payload = {"content": "旧文档原文"}
        else:
            raise AssertionError(f"Unexpected business API path: {path}")
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload, ensure_ascii=False))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.add_init_script("""
                const originalFetch = window.fetch.bind(window);
                window.__delayApi = (fragment) => {
                  window.__delayFragment = fragment;
                  window.__pendingRequest = false;
                  window.__settledRequest = false;
                };
                window.fetch = (input, options = {}) => {
                  if (window.__delayFragment && String(input).includes(window.__delayFragment)) {
                    window.__delayFragment = null;
                    window.__pendingRequest = true;
                    return new Promise((resolve, reject) => {
                      window.__releaseRequest = () => originalFetch(input, options).then((response) => {
                        window.__settledRequest = true;
                        resolve(response);
                      }, reject);
                    });
                  }
                  return originalFetch(input, options);
                };
            """)
            page.route("**/api/business/**", api)
            page.goto(base_url, wait_until="networkidle")
            page.get_by_label("检索已解析的业务文档").fill("原文")
            page.get_by_role("button", name="检索", exact=True).click()
            page.get_by_role("button", name="查找块级候选依据").click()
            page.get_by_role("button", name="冻结此块原文").wait_for()

            page.evaluate("window.__delayApi('/revisions/rev-a/evidence')")
            page.get_by_role("button", name="冻结此块原文").click()
            page.wait_for_function("window.__pendingRequest === true")
            page.get_by_role("button", name="查看 second.pdf，已解析").click()
            page.locator("#detail-content h2").get_by_text("second.pdf").wait_for()
            before = calls.count(f"/documents/{documents[0]['id']}/revisions")
            page.evaluate("window.__releaseRequest()")
            page.wait_for_function("window.__settledRequest === true")
            page.get_by_text("原文已冻结到原解析修订；当前文档保持不变。").wait_for()
            assert writes == ["/revisions/rev-a/evidence"]
            assert calls.count(f"/documents/{documents[0]['id']}/revisions") == before
            assert page.locator("#detail-content h2").inner_text() == "second.pdf"

            page.evaluate(f"window.__delayApi('/documents/{documents[0]['id']}/revisions')")
            page.get_by_role("button", name="打开并读取此块").click()
            page.wait_for_function("window.__pendingRequest === true")
            page.get_by_role("button", name="查看 second.pdf，已解析").click()
            page.evaluate("window.__releaseRequest()")
            page.wait_for_function("window.__settledRequest === true")
            page.wait_for_timeout(100)
            assert "/revisions/rev-a/content" not in calls
            assert page.locator("#detail-content h2").inner_text() == "second.pdf"

            revisions_fail = True
            page.get_by_role("button", name="检索", exact=True).click()
            page.get_by_role("button", name="查找块级候选依据").click()
            page.get_by_role("button", name="冻结此块原文").click()
            page.get_by_text("原文已冻结，但修订记录不可用：revisions_unavailable").wait_for()
            assert writes == ["/revisions/rev-a/evidence"] * 2
            assert page.get_by_role("button", name="冻结此块原文").is_disabled()
            assert not errors, errors
            print("Playwright search action races passed: accepted freeze survives stale navigation and revision read failure")
        finally:
            browser.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18088")
