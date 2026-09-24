"""Upload transport and accepted-response failures must not be reported as definite rejection."""

from __future__ import annotations

import json
import sys

from playwright.sync_api import sync_playwright


def main(base_url: str) -> None:
    upload_calls = 0
    request_keys: list[str] = []

    def api(route: object) -> None:
        nonlocal upload_calls
        request = route.request
        path = request.url.split("/api/business", 1)[1].split("?", 1)[0]
        if path == "/capabilities":
            payload = {"max_upload_bytes": 1000000, "parseable_extensions": ["pdf"],
                       "tiered_extensions": ["pdf"], "flash_only_extensions": [],
                       "tiers": ["flash", "basic", "standard", "advanced"]}
        elif path == "/templates":
            payload = []
        elif path == "/documents" and request.method == "GET":
            payload = {"items": [], "total": 0, "limit": 20, "offset": 0}
        elif path == "/documents" and request.method == "POST":
            upload_calls += 1
            request_keys.append(request.headers.get("idempotency-key", ""))
            if upload_calls == 1:
                route.abort("failed")
            elif upload_calls == 2:
                route.fulfill(status=202, content_type="application/json", body="{broken")
            elif upload_calls == 3:
                route.fulfill(status=202, content_type="application/json", body="{}")
            else:
                route.fulfill(status=202, content_type="application/json", body='{"task":{"status":"submitted"}}')
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
            page.locator("#files").set_input_files([
                {"name": "lost-response.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-first"},
                {"name": "invalid-response.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-second"},
                {"name": "missing-task.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-third"},
            ])
            page.get_by_role("button", name="开始上传与解析").click()
            page.wait_for_function("document.querySelectorAll('#upload-feedback .feedback-item').length === 3"
                                   " && !document.querySelector('#files').disabled")
            feedback = page.locator("#upload-feedback").inner_text()
            assert "lost-response.pdf · 上传结果未确认" in feedback
            assert "invalid-response.pdf · 上传结果未确认" in feedback
            assert "missing-task.pdf · 上传已受理，但任务状态未返回" in feedback
            assert feedback.count("先刷新文档列表核对") == 2
            assert upload_calls == 3, "The UI must not automatically repeat an uncertain upload"
            assert all(len(key) == 32 for key in request_keys)
            assert len(set(request_keys)) == 3
            page.locator("#upload-feedback .feedback-item").first.get_by_role(
                "button", name="使用同一请求键重试",
            ).click()
            page.get_by_text("lost-response.pdf · 已受理 · 解析中").wait_for()
            assert upload_calls == 4
            assert request_keys[3] == request_keys[0]
            assert not errors, errors
            print("Playwright unknown upload outcome passed: no false rejection or automatic retransmission")
        finally:
            browser.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18088")
