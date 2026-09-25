"""A lost cancellation response must not leave a repeat-POST button."""

from __future__ import annotations

import json
import sys
import time

from playwright.sync_api import sync_playwright


def main(base_url: str) -> None:
    now = int(time.time() * 1000)
    document = {
        "id": "a" * 32, "original_name": "cancel.pdf", "sha256": "b" * 64,
        "size": 16, "created_at_ms": now, "template_code": None, "template_version": None,
    }
    task = {
        "id": "c" * 32, "document_id": document["id"], "requested_tier": "flash",
        "actual_tier": "flash", "status": "submitted", "error_code": None,
        "cancel_effect": None, "created_at_ms": now, "updated_at_ms": now,
    }
    calls = {"cancel": 0, "probe": 0}
    probe_available = {"value": False}

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
        elif path == f"/tasks/{task['id']}/cancel":
            assert request.method == "POST"
            calls["cancel"] += 1
            route.fulfill(status=503, content_type="application/json", body='{"detail":"response_lost"}')
            return
        elif path == f"/tasks/{task['id']}":
            assert request.method == "GET"
            calls["probe"] += 1
            if not probe_available["value"]:
                route.fulfill(status=503, content_type="application/json", body='{"detail":"probe_unavailable"}')
                return
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
            page.get_by_role("button", name="查看 cancel.pdf，解析中").click()
            page.once("dialog", lambda dialog: dialog.accept())
            page.get_by_role("button", name="取消业务任务").click()
            page.get_by_text("取消结果未确认，任务状态也暂不可读", exact=False).wait_for()
            page.get_by_role("button", name="核对取消状态").wait_for()
            assert calls == {"cancel": 1, "probe": 1}
            page.get_by_role("button", name="刷新列表").click()
            page.get_by_role("button", name="核对取消状态").wait_for()
            assert calls["cancel"] == 1
            saved = page.evaluate("JSON.parse(localStorage.getItem('mineru.business.pendingCancellations.v1'))")
            assert saved == [{"taskId": task["id"], "state": "probe"}]
            page.reload(wait_until="networkidle")
            page.get_by_role("button", name="查看 cancel.pdf，解析中").click()
            page.get_by_role("button", name="核对取消状态").wait_for()
            assert calls["cancel"] == 1, "reload must not offer an unguarded cancellation POST"
            probe_available["value"] = True
            page.get_by_role("button", name="核对取消状态").focus()
            page.keyboard.press("Enter")
            repeated = page.get_by_role("button", name="再次取消（上次结果未确认）")
            repeated.wait_for()
            focused = page.evaluate("""() => ({
                tag: document.activeElement?.tagName,
                text: document.activeElement?.textContent,
                action: document.activeElement?.dataset.taskActionFocus,
            })""")
            assert repeated.evaluate("node => document.activeElement === node"), (
                f"cancellation check lost keyboard focus: {focused}"
            )
            assert repeated.evaluate("node => getComputedStyle(node).outlineStyle !== 'none'")
            assert calls == {"cancel": 1, "probe": 2}, "read-only check repeated cancellation POST"
            saved = page.evaluate("JSON.parse(localStorage.getItem('mineru.business.pendingCancellations.v1'))")
            assert saved == [{"taskId": task["id"], "state": "known"}]
            page.once("dialog", lambda dialog: dialog.dismiss())
            page.get_by_role("button", name="再次取消（上次结果未确认）").click()
            assert calls["cancel"] == 1
            task.update(status="cancelled", cancel_effect="may_continue", updated_at_ms=now + 1)
            page.get_by_role("button", name="刷新列表").click()
            page.get_by_role("button", name="查看 cancel.pdf，已取消").wait_for()
            assert page.evaluate("JSON.parse(localStorage.getItem('mineru.business.pendingCancellations.v1'))") == []
            task.update(status="submitted", cancel_effect=None, updated_at_ms=now + 2)
            page.get_by_role("button", name="刷新列表").click()
            page.get_by_role("button", name="查看 cancel.pdf，解析中").click()
            page.evaluate("""() => {
              const write = Storage.prototype.setItem;
              Storage.prototype.setItem = function (key, value) {
                if (key === 'mineru.business.pendingCancellations.v1') throw new Error('storage_blocked');
                return write.call(this, key, value);
              };
            }""")
            page.once("dialog", lambda dialog: dialog.accept())
            page.get_by_role("button", name="取消业务任务").click()
            page.get_by_text("浏览器无法保存待核对的取消请求", exact=False).wait_for()
            assert calls["cancel"] == 1, "unpersistable intent must not send a cancellation POST"
            assert not errors, errors
            print("Playwright cancellation unknown-result recovery passed")
        finally:
            browser.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18088")
