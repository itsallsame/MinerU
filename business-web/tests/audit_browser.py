"""Playwright global audit feed: pagination, safe text, and run navigation."""

from __future__ import annotations

import json
import sys
import time

from playwright.sync_api import sync_playwright


def main(base_url: str) -> None:
    now = int(time.time() * 1000)
    document = {
        "id": "d" * 32, "original_name": "notice.html", "sha256": "a" * 64,
        "size": 12, "created_at_ms": now, "template_code": "official_document", "template_version": 1,
    }
    revision = {
        "id": "r" * 32, "document_id": document["id"], "short_id": "a" * 12,
        "tier": "flash", "page_range": "1", "producer_version": "4.0.6",
        "model_ref": None, "created_at_ms": now,
    }
    run = {
        "id": "e" * 32, "revision_id": revision["id"], "template_code": "official_document",
        "template_version": 1, "status": "done", "error_code": None,
        "created_at_ms": now, "updated_at_ms": now,
    }
    events = [
        {"id": "n" * 32, "action": "result_confirmed", "source": "skill", "old_value": None,
         "new_value": "f" * 64, "reason": None},
        {"id": "o" * 32, "action": "field_decided", "source": "web", "old_value": None,
         "new_value": "<img src=x onerror=alert(1)>", "reason": "原文核对"},
    ]
    calls: list[str] = []

    def api(route: object) -> None:
        path = route.request.url.split("/api/business", 1)[1]
        calls.append(path)
        base = path.split("?", 1)[0]
        if base == "/capabilities":
            payload = {"max_upload_bytes": 1000000, "parseable_extensions": ["pdf"],
                       "tiered_extensions": ["pdf"], "flash_only_extensions": [],
                       "tiers": ["flash", "basic", "standard", "advanced"]}
        elif base == "/templates":
            payload = [{"code": "official_document", "name": "公文", "version": 1,
                        "built_in": True, "enabled": True, "fields": [], "created_at_ms": now}]
        elif base == "/templates/official_document":
            payload = {"code": "official_document", "name": "公文", "version": 1,
                       "built_in": True, "enabled": True, "fields": [], "created_at_ms": now}
        elif base == "/documents":
            payload = {"items": [], "total": 0, "limit": 20, "offset": 0}
        elif base == f"/documents/{document['id']}":
            payload = document
        elif base == f"/documents/{document['id']}/revisions":
            payload = [revision]
        elif base == f"/revisions/{revision['id']}/extractions":
            payload = [run]
        elif base == f"/revisions/{revision['id']}/evidence":
            payload = []
        elif base == f"/extractions/{run['id']}":
            payload = {"run": run, "candidates": [], "issues": []}
        elif base in (f"/extractions/{run['id']}/decisions", f"/extractions/{run['id']}/results"):
            payload = []
        elif base == f"/extractions/{run['id']}/audit":
            payload = []
        elif base == "/audit":
            index = 1 if "before=" in path else 0
            payload = {"items": [{
                "event": {**events[index], "run_id": run["id"], "target_id": "t" * 32,
                          "created_at_ms": now},
                "document_id": document["id"], "document_name": document["original_name"],
                "revision_id": revision["id"],
            }], "next_before": events[0]["id"] if index == 0 else None}
        else:
            raise AssertionError(f"Unexpected API path: {path}")
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/business/**", api)
            page.goto(base_url, wait_until="networkidle")
            assert not any(path.startswith("/audit") for path in calls)
            page.locator("#audit-panel > summary").click()
            page.locator(".audit-record").first.wait_for()
            assert page.locator(".audit-record").count() == 1
            assert page.locator(".audit-record").first.get_by_text("入口：skill", exact=False).count() == 1
            page.get_by_role("button", name="加载更早记录").click()
            page.locator(".audit-record").nth(1).wait_for()
            assert page.locator(".audit-record").count() == 2
            page.locator(".audit-record").last.locator("details > summary").click()
            assert page.locator(".audit-record img").count() == 0
            assert page.locator(".audit-record").last.get_by_text("<img src=x onerror=alert(1)>", exact=False).count() == 1
            page.locator(".audit-record").last.get_by_role("button", name="打开对应提取运行").click()
            page.locator('select[aria-label="选择提取运行"]').wait_for()
            assert page.locator('select[aria-label="选择提取运行"]').input_value() == run["id"]
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            assert not errors, errors
            print("Playwright audit feed passed: lazy pagination, safe text, run navigation, narrow viewport")
        finally:
            browser.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18088")
