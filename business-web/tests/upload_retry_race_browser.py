"""Upload settings and task-retry feedback remain stable across async navigation."""

from __future__ import annotations

import json
import sys
import time

from playwright.sync_api import sync_playwright


def main(base_url: str) -> None:
    now = int(time.time() * 1000)
    documents = [
        {"id": char * 32, "original_name": name, "sha256": char * 64, "size": 16,
         "created_at_ms": now, "template_code": None, "template_version": None}
        for char, name in (("a", "failed.pdf"), ("b", "current.pdf"))
    ]
    tasks = [
        {"id": char * 32, "document_id": doc["id"], "requested_tier": "flash", "actual_tier": "flash",
         "status": status, "error_code": "parse_failed" if status == "failed" else None,
         "created_at_ms": now, "updated_at_ms": now}
        for char, doc, status in (("c", documents[0], "failed"), ("d", documents[1], "done"))
    ]
    uploaded: list[bytes] = []
    retry_calls = 0

    def api(route: object) -> None:
        nonlocal retry_calls
        request = route.request
        path = request.url.split("/api/business", 1)[1].split("?", 1)[0]
        if path == "/capabilities":
            payload = {"max_upload_bytes": 1000000, "parseable_extensions": ["pdf"],
                       "tiered_extensions": ["pdf"], "flash_only_extensions": [],
                       "tiers": ["flash", "basic", "standard", "advanced"]}
        elif path == "/templates":
            payload = [{"code": "my_report", "name": "我的报告", "version": 1, "enabled": True,
                        "built_in": False, "fields": [], "created_at_ms": now}]
        elif path == "/documents" and request.method == "GET":
            payload = {"items": [{"document": doc, "task": task} for doc, task in zip(documents, tasks)],
                       "total": 2, "limit": 20, "offset": 0}
        elif path == "/documents" and request.method == "POST":
            uploaded.append(request.post_data_buffer)
            payload = {"document": documents[0], "task": {**tasks[0], "status": "submitted"}}
        elif path in (f"/documents/{doc['id']}/revisions" for doc in documents):
            payload = []
        elif path.endswith("/source"):
            route.fulfill(status=200, content_type="application/pdf", body=b"%PDF-1.4\n")
            return
        elif path == f"/tasks/{tasks[0]['id']}/retry":
            retry_calls += 1
            if retry_calls == 1:
                route.fulfill(status=503, content_type="application/json", body='{"detail":"retry_unavailable"}')
                return
            tasks[0] = {**tasks[0], "status": "submitted", "error_code": None}
            payload = tasks[0]
        else:
            raise AssertionError(f"Unexpected API call: {request.method} {path}")
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload, ensure_ascii=False))

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
                  window.__pendingWrite = false;
                  window.__settledWrite = false;
                };
                window.fetch = (input, options = {}) => {
                  if (window.__delayFragment && String(input).includes(window.__delayFragment)) {
                    window.__delayFragment = null;
                    window.__pendingWrite = true;
                    return new Promise((resolve, reject) => {
                      window.__releaseWrite = () => originalFetch(input, options).then((response) => {
                        window.__settledWrite = true;
                        resolve(response);
                      }, reject);
                    });
                  }
                  return originalFetch(input, options);
                };
            """)
            page.route("**/api/business/**", api)
            page.goto(base_url, wait_until="networkidle")
            page.locator("#upload-tier").select_option("standard")
            page.locator("#upload-template").select_option("my_report")
            page.locator("#files").set_input_files([
                {"name": "one.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-one"},
                {"name": "two.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-two"},
            ])
            page.evaluate("window.__delayNext('/api/business/documents')")
            page.get_by_role("button", name="开始上传与解析").click()
            page.wait_for_function("window.__pendingWrite === true")
            assert page.locator("#files").is_disabled()
            assert page.locator("#upload-tier").is_disabled()
            assert page.locator("#upload-template").is_disabled()
            page.get_by_role("button", name="查看 current.pdf，已解析").click()
            page.locator("#detail-content h2").get_by_text("current.pdf").wait_for()
            page.evaluate("window.__releaseWrite()")
            page.wait_for_function("window.__settledWrite === true")
            page.get_by_text("two.pdf · 已受理").wait_for()
            assert len(uploaded) == 2
            assert all(b'name="tier"' in body and b"standard" in body for body in uploaded)
            assert all(b'name="template_code"' in body and b"my_report" in body for body in uploaded)
            assert page.locator("#detail-content h2").inner_text() == "current.pdf"
            assert page.locator("#files").is_enabled()

            page.get_by_role("button", name="查看 failed.pdf，失败").click()
            page.get_by_role("button", name="重新提交任务").wait_for()
            page.evaluate(f"window.__delayNext('/tasks/{tasks[0]['id']}/retry')")
            page.get_by_role("button", name="重新提交任务").click()
            page.wait_for_function("window.__pendingWrite === true")
            page.get_by_role("button", name="查看 current.pdf，已解析").click()
            page.evaluate("window.__releaseWrite()")
            page.wait_for_function("window.__settledWrite === true")
            page.wait_for_timeout(100)
            assert page.locator("#detail-content h2").inner_text() == "current.pdf"
            assert page.get_by_text("重试失败：retry_unavailable").count() == 0

            page.get_by_role("button", name="查看 failed.pdf，失败").click()
            page.get_by_role("button", name="重新提交任务").click()
            page.get_by_role("button", name="查看 failed.pdf，解析中").wait_for()
            assert retry_calls == 2
            assert not errors, errors
            print("Playwright upload/retry races passed: batch settings frozen; stale retry error hidden")
        finally:
            browser.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18088")
