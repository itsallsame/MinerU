"""Task polling updates current rows without replaying stale selection or errors."""

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
        for char, name in (("a", "pending.pdf"), ("b", "current.pdf"))
    ]
    tasks = [
        {"id": char * 32, "document_id": doc["id"], "requested_tier": "flash",
         "actual_tier": "flash", "status": status, "error_code": None,
         "created_at_ms": now, "updated_at_ms": now}
        for char, doc, status in (("c", documents[0], "submitted"), ("d", documents[1], "done"))
    ]
    poll_calls = 0
    a_revision_reads = 0
    b_revision_reads = 0

    def api(route: object) -> None:
        nonlocal poll_calls, a_revision_reads, b_revision_reads
        request = route.request
        path = request.url.split("/api/business", 1)[1].split("?", 1)[0]
        if path == "/capabilities":
            payload = {"max_upload_bytes": 1000000, "parseable_extensions": ["pdf"],
                       "tiered_extensions": ["pdf"], "flash_only_extensions": [],
                       "tiers": ["flash", "basic", "standard", "advanced"]}
        elif path == "/templates":
            payload = []
        elif path == "/documents":
            status_filter = request.url.split("status=", 1)[1].split("&", 1)[0] if "status=" in request.url else ""
            items = [{"document": doc, "task": task} for doc, task in zip(documents, tasks)
                     if not status_filter or task["status"] == status_filter]
            payload = {"items": items, "total": len(items), "limit": 20, "offset": 0}
        elif path == f"/tasks/{tasks[0]['id']}":
            poll_calls += 1
            if poll_calls == 2:
                route.fulfill(status=503, content_type="application/json", body='{"detail":"offline"}')
                return
            tasks[0] = {**tasks[0], "status": "done"}
            payload = tasks[0]
        elif path in (f"/documents/{doc['id']}/revisions" for doc in documents):
            if path == f"/documents/{documents[0]['id']}/revisions":
                a_revision_reads += 1
            else:
                b_revision_reads += 1
            payload = []
        elif path.endswith("/source"):
            route.fulfill(status=200, content_type="application/pdf", body=b"%PDF-1.4\n")
            return
        else:
            raise AssertionError(f"Unexpected API call: {path}")
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

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
                };
                window.fetch = (input, options = {}) => {
                  if (window.__delayFragment && String(input).includes(window.__delayFragment)) {
                    window.__delayFragment = null;
                    window.__pendingRead = true;
                    return new Promise((resolve, reject) => {
                      window.__releaseRead = () => originalFetch(input, options).then(resolve, reject);
                    });
                  }
                  return originalFetch(input, options);
                };
                window.__intervals = [];
                window.setInterval = (callback) => {
                  window.__intervals.push(() => {
                    window.__pollPromise = Promise.resolve(callback());
                    return window.__pollPromise;
                  });
                  return window.__intervals.length;
                };
            """)
            page.route("**/api/business/**", api)
            page.goto(base_url, wait_until="networkidle")
            page.get_by_role("button", name="查看 current.pdf，已解析").click()
            page.locator("#detail-content h2").get_by_text("current.pdf").wait_for()
            page.evaluate(f"window.__delayNext('/tasks/{tasks[0]['id']}')")
            page.evaluate("void window.__intervals[0]()")
            page.wait_for_function("window.__pendingRead === true")
            page.get_by_role("button", name="查看 pending.pdf，解析中").click()
            page.get_by_role("button", name="查看 current.pdf，已解析").click()
            reads_before_release = b_revision_reads
            page.evaluate("window.__releaseRead()")
            page.evaluate("window.__pollPromise")
            page.get_by_role("button", name="查看 pending.pdf，已解析").wait_for()
            assert b_revision_reads == reads_before_release, "old poll reselected a newer document"
            assert page.locator("#detail-content h2").inner_text() == "current.pdf"

            tasks[0] = {**tasks[0], "status": "submitted"}
            page.get_by_role("button", name="刷新列表").click()
            page.get_by_role("button", name="查看 pending.pdf，解析中").wait_for()
            page.evaluate(f"window.__delayNext('/tasks/{tasks[0]['id']}')")
            page.evaluate("void window.__intervals[0]()")
            page.wait_for_function("window.__pendingRead === true")
            page.locator("#status-filter").select_option("done")
            page.get_by_role("button", name="查看 current.pdf，已解析").wait_for()
            page.evaluate("window.__releaseRead()")
            page.evaluate("window.__pollPromise")
            assert page.locator("#global-error").is_hidden(), "old poll error leaked into a new filter"
            assert page.locator("#detail-content h2").inner_text() == "current.pdf"
            assert poll_calls == 2

            page.locator("#status-filter").select_option("")
            with page.expect_response(f"**/documents/{documents[0]['id']}/revisions"):
                page.get_by_role("button", name="查看 pending.pdf，解析中").click()
            page.locator("#detail-content h2").get_by_text("pending.pdf").wait_for()
            reads_before_completion = a_revision_reads
            page.evaluate("window.__intervals[0]()")
            page.get_by_role("button", name="查看 pending.pdf，已解析").wait_for()
            assert a_revision_reads > reads_before_completion, "selected completed task did not reload revisions"
            assert page.locator("#detail-content h2").inner_text() == "pending.pdf"
            assert poll_calls == 3
            assert not errors, errors
            print("Playwright task polling passed: completion refreshes revisions; stale selection/error ignored")
        finally:
            browser.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18088")
