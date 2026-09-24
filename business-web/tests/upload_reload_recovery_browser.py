"""A lost upload response survives reload without creating a second request key."""

from __future__ import annotations

import json
import sys

from playwright.sync_api import sync_playwright


def main(base_url: str) -> None:
    post_keys: list[str] = []
    lookup_keys: list[str] = []

    def api(route: object) -> None:
        request = route.request
        path = request.url.split("/api/business", 1)[1].split("?", 1)[0]
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
        elif path == "/documents" and request.method == "GET":
            payload = {"items": [], "total": 0, "limit": 20, "offset": 0}
        elif path == "/documents" and request.method == "POST":
            key = request.headers.get("idempotency-key", "")
            post_keys.append(key)
            if len(post_keys) <= 2:
                route.abort("failed")
            else:
                route.fulfill(status=202, content_type="application/json", body='{"task":{"status":"submitted"}}')
            return
        elif path.startswith("/upload-requests/"):
            key = path.rsplit("/", 1)[1]
            lookup_keys.append(key)
            if key == post_keys[0]:
                route.fulfill(status=200, content_type="application/json", body='{"task":{"status":"submitted"}}')
            else:
                route.fulfill(status=404, content_type="application/json", body='{"detail":"not found"}')
            return
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
            page.locator("#files").set_input_files(
                [
                    {"name": "accepted.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-one"},
                    {"name": "pending.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-two"},
                ]
            )
            page.get_by_role("button", name="开始上传与解析").click()
            page.wait_for_function(
                "document.querySelectorAll('#upload-feedback .feedback-item').length === 2"
                " && !document.querySelector('#files').disabled"
            )
            assert len(post_keys) == 2 and len(set(post_keys)) == 2
            assert len(page.evaluate("JSON.parse(sessionStorage.getItem('mineru.business.pendingUploads.v1'))")) == 2

            page.reload(wait_until="networkidle")
            page.get_by_text("accepted.pdf · 已受理 · 解析中").wait_for()
            pending = page.locator("#upload-feedback .feedback-item").filter(has_text="pending.pdf")
            pending.get_by_text("尚未查到该请求").wait_for()
            assert len(post_keys) == 2, "A reload or 404 lookup must not auto-retransmit"
            assert set(lookup_keys) == set(post_keys)
            saved = page.evaluate("JSON.parse(sessionStorage.getItem('mineru.business.pendingUploads.v1'))")
            assert [record["requestKey"] for record in saved] == [post_keys[1]]

            pending.get_by_label("重选 pending.pdf 以重试同一次上传").set_input_files(
                {"name": "pending.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-two"},
            )
            pending.get_by_role("button", name="用重选文件和原键重试").click()
            page.get_by_text("pending.pdf · 已受理 · 解析中").wait_for()
            assert post_keys == [post_keys[0], post_keys[1], post_keys[1]]
            assert page.evaluate("JSON.parse(sessionStorage.getItem('mineru.business.pendingUploads.v1'))") == []
            assert not errors, errors
            print("Playwright upload reload recovery passed: lookup first, same-key manual retry only")
        finally:
            browser.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18088")
