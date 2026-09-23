"""Playwright discovery and progressive-read flow against the real Web bundle."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


def main(base_url: str, screenshot: Path | None = None) -> None:
    now = int(time.time() * 1000)
    document = {
        "id": "a" * 32, "original_name": "notice.html", "sha256": "b" * 64,
        "size": 100, "created_at_ms": now, "template_code": None, "template_version": None,
    }
    revision = {
        "id": "rev-1", "document_id": document["id"], "short_id": document["sha256"][:7],
        "tier": "flash", "page_range": "1",
        "producer_version": "4.0.6", "model_ref": None, "created_at_ms": now,
    }
    first = f"doc:{document['sha256'][:7]}/tier:flash/page:1"
    second = f"doc:{document['sha256'][:7]}/tier:flash/page:1/block:2"
    calls: list[str] = []

    def fulfill(route: object, payload: object) -> None:
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload, ensure_ascii=False))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/business/documents?*", lambda route: fulfill(route, {
                "items": [], "total": 0, "limit": 20, "offset": 0,
            }))
            page.route("**/api/business/search?*", lambda route: fulfill(route, {
                "items": [{
                    "document": document, "revision_id": revision["id"], "tier": "flash",
                    "snippet": "年度通知摘要", "state": "current_index_unconfirmed",
                }],
                "scan_complete": True, "scanned_doclib_hits": 1,
            }))
            page.route("**/api/business/documents/*/revisions", lambda route: fulfill(route, [revision]))
            page.route("**/api/business/revisions/rev-1/extractions", lambda route: fulfill(route, []))
            page.route("**/api/business/revisions/rev-1/evidence", lambda route: fulfill(route, []))
            page.route("**/api/business/revisions/rev-1/search?*", lambda route: fulfill(route, {
                "revision_id": "rev-1", "scanned_pages": 1, "next_page": None,
                "items": [{"locator": first, "page_no": 1, "snippet": "年度通知原文",
                           "state": "historical_parse_unconfirmed"}],
            }))

            def read(route: object) -> None:
                calls.append(route.request.url)
                locator = second if "block%3A2" in route.request.url else first
                fulfill(route, {
                    "document_id": document["id"], "revision_id": revision["id"],
                    "locator": locator, "tier": "flash",
                    "content": "第二段历史内容" if locator == second else "第一段历史内容",
                    "truncated": locator == first, "next_locator": second if locator == first else None,
                    "state": "historical_parse_unconfirmed",
                })

            page.route("**/api/business/revisions/rev-1/content?*", read)
            page.goto(base_url, wait_until="networkidle")
            page.get_by_label("检索已解析的业务文档").fill("年度通知")
            page.get_by_role("button", name="检索", exact=True).click()
            page.get_by_text("当前索引预览，未人工确认：年度通知摘要").wait_for()
            page.get_by_role("button", name="查找原文页").click()
            page.get_by_text("机器解析命中，未人工确认：年度通知原文").wait_for()
            page.get_by_role("button", name="打开并读取此页").click()
            page.get_by_text("检索未返回任务状态").wait_for()
            page.get_by_text("第一段历史内容").wait_for()
            page.get_by_role("button", name="继续读取下一段").click()
            page.get_by_text("第二段历史内容").wait_for()
            assert len(calls) == 2
            assert "doc%3Abbbbbbb" in calls[0]
            if screenshot:
                page.screenshot(path=str(screenshot), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            assert not errors, errors
            print("Playwright business discovery passed: index preview, business document, historical continuation")
        finally:
            browser.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18088", Path(sys.argv[2]) if len(sys.argv) > 2 else None)
