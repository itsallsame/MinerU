"""Playwright outage/recovery checks against the offline Web bundle."""

from __future__ import annotations

import json
import sys
import time

from playwright.sync_api import sync_playwright


def main(base_url: str) -> None:
    service = {"available": False, "list_available": True}
    calls: list[str] = []
    now = int(time.time() * 1000)
    record = {
        "id": "a" * 32, "original_name": "report.pdf", "sha256": "b" * 64,
        "size": 12, "created_at_ms": now, "template_code": None, "template_version": None,
    }
    task = {
        "id": "c" * 32, "document_id": record["id"], "requested_tier": "standard",
        "actual_tier": "standard", "status": "done", "error_code": None,
        "created_at_ms": now, "updated_at_ms": now,
    }

    def route_api(route: object) -> None:
        path = route.request.url.split("/api/business", 1)[1].split("?", 1)[0]
        calls.append(path)
        if not service["available"] or (path == "/documents" and not service["list_available"]):
            route.fulfill(status=503, content_type="application/json", body='{"detail":"service_unavailable"}')
            return
        if path == "/capabilities":
            payload = {
                "max_upload_bytes": 1000000, "parseable_extensions": ["pdf"],
                "tiered_extensions": ["pdf"], "flash_only_extensions": [],
                "tiers": ["flash", "basic", "standard", "advanced"],
            }
        elif path == "/templates":
            payload = []
        elif path == "/documents":
            payload = {"items": [{"document": record, "task": task}], "total": 1, "limit": 20, "offset": 0}
        elif path == f"/documents/{record['id']}/revisions":
            payload = []
        elif path == f"/documents/{record['id']}/source":
            route.fulfill(status=200, content_type="application/pdf", body=b"%PDF-1.4\n")
            return
        else:
            raise AssertionError(f"Unexpected API path: {path}")
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/business/**", route_api)
            page.goto(base_url, wait_until="networkidle")
            page.get_by_text("业务服务不可用", exact=True).wait_for()
            assert page.get_by_text("文档数量未知").count() == 1
            assert page.get_by_role("button", name="开始上传与解析").is_disabled()
            assert page.get_by_role("button", name="重新连接并刷新").count() == 1

            service["available"] = True
            page.get_by_role("button", name="重新连接并刷新").click()
            page.get_by_text("业务服务已连接", exact=True).wait_for()
            page.get_by_role("button", name="查看 report.pdf，已解析").wait_for()
            page.locator("#files").set_input_files({"name": "report.pdf", "mimeType": "application/pdf", "buffer": b"%PDF"})
            assert page.get_by_role("button", name="开始上传与解析").is_enabled()
            assert calls.count("/capabilities") == 2 and calls.count("/templates") == 2

            page.get_by_role("button", name="查看 report.pdf，已解析").click()
            page.get_by_text("处理状态", exact=True).wait_for()
            service["list_available"] = False
            page.get_by_role("button", name="刷新列表").click()
            page.get_by_text("文档状态暂不可用").wait_for()
            assert page.get_by_role("button", name="查看 report.pdf，已解析").count() == 0
            assert page.get_by_text("文档数量未知").count() == 1
            assert page.get_by_role("button", name="开始上传与解析").is_disabled()
            assert page.locator("#previous-page").is_disabled() and page.locator("#next-page").is_disabled()

            service["list_available"] = True
            page.get_by_role("button", name="重新连接并刷新").click()
            page.get_by_role("button", name="查看 report.pdf，已解析").wait_for()
            assert page.get_by_role("button", name="开始上传与解析").is_enabled()
            assert page.locator("#global-error").is_hidden()
            assert not errors, errors
            print("Playwright outage recovery passed: open service retry, metadata reload, stale-state clearing")
        finally:
            browser.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18088")
