"""Delayed evidence/audit navigation must not replace a newer document selection."""

from __future__ import annotations

import json
import sys
import time

from playwright.sync_api import sync_playwright


def main(base_url: str) -> None:
    now = int(time.time() * 1000)
    documents = [
        {"id": char * 32, "original_name": name, "sha256": char * 64,
         "size": 16, "created_at_ms": now, "template_code": None, "template_version": None}
        for char, name in (("a", "old.pdf"), ("b", "current.pdf"))
    ]
    tasks = [
        {"id": char * 32, "document_id": doc["id"], "requested_tier": "flash",
         "actual_tier": "flash", "status": "done", "error_code": None,
         "created_at_ms": now, "updated_at_ms": now}
        for char, doc in (("c", documents[0]), ("d", documents[1]))
    ]
    evidence = {
        "id": "evidence-old", "document_id": documents[0]["id"],
        "revision_id": "revision-old", "page_no": 1,
    }

    def api(route: object) -> None:
        path = route.request.url.split("/api/business", 1)[1].split("?", 1)[0]
        if path == "/capabilities":
            payload = {"max_upload_bytes": 1000000, "parseable_extensions": ["pdf"],
                       "tiered_extensions": ["pdf"], "flash_only_extensions": [],
                       "tiers": ["flash", "basic", "standard", "advanced"]}
        elif path == "/templates":
            payload = []
        elif path == "/documents":
            payload = {"items": [{"document": doc, "task": task}
                                 for doc, task in zip(documents, tasks)],
                       "total": 2, "limit": 20, "offset": 0}
        elif path == f"/documents/{documents[0]['id']}":
            payload = documents[0]
        elif path == "/evidence/evidence-old":
            payload = evidence
        elif path == "/audit":
            payload = {"items": [{
                "event": {"id": "e" * 32, "run_id": "r" * 32,
                          "action": "result_confirmed", "source": "web",
                          "old_value": None, "new_value": "confirmed",
                          "reason": None, "created_at_ms": now},
                "document_id": documents[0]["id"],
                "document_name": documents[0]["original_name"],
                "revision_id": "revision-old",
            }], "next_before": None}
        elif path in (f"/documents/{doc['id']}/revisions" for doc in documents):
            payload = []
        elif path.endswith("/source"):
            route.fulfill(status=200, content_type="application/pdf", body=b"%PDF-1.4\n")
            return
        else:
            raise AssertionError(f"Unexpected API call: {path}")
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(payload, ensure_ascii=False))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.add_init_script("""
                const originalFetch = window.fetch.bind(window);
                window.__delayNext = (fragment) => {
                  window.__delayFragment = fragment;
                  window.__pendingRead = false;
                  window.__settledRead = false;
                };
                window.fetch = (input, options = {}) => {
                  if (window.__delayFragment && String(input).includes(window.__delayFragment)) {
                    window.__delayFragment = null;
                    window.__pendingRead = true;
                    return new Promise((resolve, reject) => {
                      window.__releaseRead = () => originalFetch(input, options).then((response) => {
                        window.__settledRead = true;
                        resolve(response);
                      }, reject);
                    });
                  }
                  return originalFetch(input, options);
                };
            """)
            page.route("**/api/business/**", api)
            page.goto(base_url, wait_until="networkidle")
            page.evaluate("window.__delayNext('/evidence/evidence-old')")
            page.evaluate("window.location.hash = '#evidence=evidence-old'")
            page.wait_for_function("window.__pendingRead === true")
            page.get_by_role("button", name="查看 current.pdf，已解析").click()
            page.locator("#detail-content h2").get_by_text("current.pdf").wait_for()
            page.evaluate("window.__releaseRead()")
            page.wait_for_function("window.__settledRead === true")
            page.wait_for_timeout(100)
            assert page.locator("#detail-content h2").inner_text() == "current.pdf"

            page.locator("#audit-panel > summary").click()
            page.locator(".audit-record").first.wait_for()
            page.evaluate(f"window.__delayNext('/documents/{documents[0]['id']}')")
            page.get_by_role("button", name="打开对应提取运行").click()
            page.wait_for_function("window.__pendingRead === true")
            page.get_by_role("button", name="查看 old.pdf，已解析").click()
            page.locator("#detail-content h2").get_by_text("old.pdf").wait_for()
            page.get_by_role("button", name="查看 current.pdf，已解析").click()
            page.locator("#detail-content h2").get_by_text("current.pdf").wait_for()
            page.evaluate("window.__releaseRead()")
            page.wait_for_function("window.__settledRead === true")
            page.wait_for_timeout(100)
            assert page.locator("#detail-content h2").inner_text() == "current.pdf"
            assert page.locator("#global-error").is_hidden()
            assert not errors, errors
            print("Playwright navigation races passed: stale evidence/audit reads cannot navigate")
        finally:
            browser.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18088")
