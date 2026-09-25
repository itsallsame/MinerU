"""Lost task-retry responses are reconciled by durable key across reload."""

from __future__ import annotations

import json
import sys
import time

from playwright.sync_api import sync_playwright


def main(base_url: str) -> None:
    now = int(time.time() * 1000)
    document = {
        "id": "a" * 32, "original_name": "retry.pdf", "sha256": "b" * 64,
        "size": 16, "created_at_ms": now, "template_code": None, "template_version": None,
    }
    task = {
        "id": "c" * 32, "document_id": document["id"], "requested_tier": "flash",
        "actual_tier": "flash", "status": "failed", "error_code": "doclib_submission_failed",
        "created_at_ms": now, "updated_at_ms": now,
    }
    mode = {"post": "accepted", "lookup": "found"}
    calls = {"retry": 0, "probe": 0}
    keys: list[str] = []

    def api(route: object) -> None:
        request = route.request
        path = request.url.split("/api/business", 1)[1].split("?", 1)[0]
        if path == "/capabilities":
            payload = {"max_upload_bytes": 1000000, "parseable_extensions": ["pdf"],
                       "tiered_extensions": ["pdf"], "flash_only_extensions": [],
                       "tiers": ["flash", "basic", "standard", "advanced"]}
        elif path == "/templates":
            payload = []
        elif path == "/documents":
            payload = {"items": [{"document": document, "task": task}], "total": 1, "limit": 20, "offset": 0}
        elif path == f"/documents/{document['id']}/revisions":
            payload = []
        elif path == f"/documents/{document['id']}/source":
            route.fulfill(status=200, content_type="application/pdf", body=b"%PDF-1.4\n")
            return
        elif path == f"/tasks/{task['id']}/retry" and request.method == "POST":
            calls["retry"] += 1
            keys.append(request.headers["idempotency-key"])
            if mode["post"] in ("accepted", "replay"):
                task.update(status="submitted", error_code=None, updated_at_ms=now + 1)
            if mode["post"] == "replay":
                payload = task
            else:
                route.fulfill(status=503, content_type="application/json", body='{"detail":"response_lost"}')
                return
        elif path.startswith("/task-retry-requests/") and request.method == "GET":
            calls["probe"] += 1
            assert path in {f"/task-retry-requests/{key}" for key in keys}
            if mode["lookup"] == "offline":
                route.fulfill(status=503, content_type="application/json", body='{"detail":"probe_unavailable"}')
                return
            if mode["lookup"] == "missing":
                route.fulfill(status=404, content_type="application/json", body='{"detail":"not_recorded"}')
                return
            payload = task
        elif path == f"/tasks/{task['id']}" and request.method == "GET":
            payload = task
        else:
            raise AssertionError(f"Unexpected API call: {request.method} {path}")
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/business/**", api)
            page.goto(base_url, wait_until="networkidle")
            page.get_by_role("button", name="查看 retry.pdf，失败").click()
            page.get_by_role("button", name="重新提交任务").click()
            page.get_by_role("button", name="查看 retry.pdf，解析中").wait_for()
            status_section = page.locator("#detail-content .detail-section").first
            assert status_section.evaluate("node => document.activeElement === node"), "accepted retry lost keyboard focus"
            assert calls == {"retry": 1, "probe": 1}
            assert len(keys[0]) == 32
            assert page.get_by_text("重试失败", exact=False).count() == 0

            task.update(status="failed", error_code="doclib_submission_failed", updated_at_ms=now + 2)
            mode.update(post="unknown", lookup="offline")
            page.get_by_role("button", name="刷新列表").click()
            page.get_by_role("button", name="查看 retry.pdf，失败").wait_for()
            page.get_by_role("button", name="重新提交任务").click()
            page.get_by_text("原重试请求键核对暂不可用", exact=False).wait_for()
            assert calls == {"retry": 2, "probe": 2}
            saved = page.evaluate("JSON.parse(localStorage.getItem('mineru.business.pendingTaskRetries.v1'))")
            assert saved == [{"taskId": task["id"], "requestKey": keys[1]}]
            page.reload(wait_until="networkidle")
            page.get_by_role("button", name="查看 retry.pdf，失败").click()
            check = page.get_by_role("button", name="按原请求键核对重试")
            assert calls["retry"] == 2, "page reload repeated a retry POST"
            mode["lookup"] = "missing"
            check.click()
            page.get_by_text("原重试请求键暂未查到", exact=False).wait_for()
            assert calls == {"retry": 2, "probe": 3}, "read-only recheck sent another POST"
            mode["post"] = "replay"
            page.once("dialog", lambda dialog: dialog.accept())
            page.get_by_role("button", name="用原键重试任务").click()
            page.get_by_role("button", name="查看 retry.pdf，解析中").wait_for()
            assert calls["retry"] == 3 and keys[2] == keys[1] and keys[1] != keys[0]
            assert page.evaluate("JSON.parse(localStorage.getItem('mineru.business.pendingTaskRetries.v1'))") == []
            assert not errors, errors
            print("Playwright keyed task retry recovery passed: reload, 404, same-key explicit replay")
        finally:
            browser.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18088")
