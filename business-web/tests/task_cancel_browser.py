"""Cancellation copy and endpoint stay honest about shared or running work."""

from __future__ import annotations

import json
import sys
import time

from playwright.sync_api import sync_playwright


def main(base_url: str) -> None:
    now = int(time.time() * 1000)
    document = {
        "id": "a" * 32, "original_name": "shared.pdf", "sha256": "b" * 64,
        "size": 16, "created_at_ms": now, "template_code": None, "template_version": None,
    }
    task = {
        "id": "c" * 32, "document_id": document["id"], "requested_tier": "flash",
        "actual_tier": "flash", "status": "submitted", "error_code": None,
        "cancel_effect": None, "created_at_ms": now, "updated_at_ms": now,
    }
    cancel_calls = 0

    def api(route: object) -> None:
        nonlocal cancel_calls, task
        path = route.request.url.split("/api/business", 1)[1].split("?", 1)[0]
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
        elif path == f"/tasks/{task['id']}/cancel":
            assert route.request.method == "POST"
            cancel_calls += 1
            task = {**task, "status": "cancelled", "cancel_effect": "may_continue"}
            payload = task
        elif path == f"/tasks/{task['id']}":
            payload = task
        else:
            raise AssertionError(f"Unexpected API call: {path}")
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/business/**", api)
            page.goto(base_url, wait_until="networkidle")
            page.get_by_role("button", name="查看 shared.pdf，解析中").click()
            cancel = page.get_by_role("button", name="取消业务任务")
            messages: list[str] = []
            page.once("dialog", lambda dialog: (messages.append(dialog.message), dialog.dismiss()))
            cancel.click()
            assert "底层计算可能继续" in messages[0]
            assert cancel_calls == 0
            page.once("dialog", lambda dialog: dialog.accept())
            cancel.click()
            page.get_by_text("业务任务已取消；共享、运行中或已完成的底层计算可能继续。").wait_for()
            assert cancel_calls == 1
            assert page.get_by_role("button", name="取消业务任务").count() == 0
            status_section = page.locator("#detail-content .detail-section").first
            assert status_section.evaluate("node => document.activeElement === node"), (
                "terminal cancellation lost keyboard focus"
            )
            assert status_section.evaluate("node => getComputedStyle(node).outlineStyle !== 'none'")
            assert not errors, errors
            print("Playwright business cancellation passed: explicit confirm and truthful shared-work result")
        finally:
            browser.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18088")
