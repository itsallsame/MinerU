"""Open a Skill evidence URL in the real business Web bundle.

Run with a live local business Web/API: python evidence_link_browser.py http://127.0.0.1:18088
"""

from __future__ import annotations

import json
import sys
import time

from playwright.sync_api import sync_playwright


def main(base_url: str) -> None:
    now = int(time.time() * 1000)
    document = {
        "id": "a" * 32, "original_name": "notice.html", "sha256": "b" * 64,
        "size": 100, "created_at_ms": now, "template_code": None, "template_version": None,
    }
    revision = {
        "id": "rev-1", "document_id": document["id"], "short_id": document["sha256"][:12],
        "tier": "flash", "page_range": "1",
        "producer_version": "4.0.6", "model_ref": None, "created_at_ms": now,
    }
    evidence = {
        "id": "evidence-1", "revision_id": revision["id"], "document_id": document["id"],
        "locator": f"doc:{document['sha256'][:12]}/tier:flash/page:1", "page_no": 1,
        "block_no": None, "bbox": None, "snippet": "冻结的历史原文片段",
        "snippet_sha256": "d" * 64, "created_at_ms": now,
        "navigation_status": "changed",
    }
    writes: list[str] = []

    def fulfill(route: object, payload: object) -> None:
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload, ensure_ascii=False))

    def guard_writes(route: object) -> None:
        if route.request.method != "GET":
            writes.append(route.request.url)
            route.abort()
        else:
            route.continue_()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/business/**", guard_writes)
            page.route("**/api/business/documents?*", lambda route: fulfill(route, {
                "items": [], "total": 0, "limit": 20, "offset": 0,
            }))
            page.route(f"**/api/business/documents/{document['id']}", lambda route: fulfill(route, document))
            page.route(f"**/api/business/documents/{document['id']}/revisions", lambda route: fulfill(route, [revision]))
            page.route("**/api/business/revisions/rev-1/extractions", lambda route: fulfill(route, []))
            page.route("**/api/business/revisions/rev-1/evidence", lambda route: fulfill(route, [evidence]))
            page.route("**/api/business/evidence/evidence-1", lambda route: fulfill(route, evidence))
            page.route("**/api/business/evidence/foreign", lambda route: fulfill(route, {
                **evidence, "id": "foreign", "revision_id": "other-revision",
            }))
            page.goto(f"{base_url}/#evidence=evidence-1", wait_until="networkidle")
            page.locator(".evidence-snippet").get_by_text("冻结的历史原文片段").wait_for()
            page.get_by_text("当前定位内容已变化").wait_for()
            assert page.get_by_role("link", name="在新页面打开此证据").get_attribute("href") == "#evidence=evidence-1"
            assert page.get_by_role("button", name="尝试跳转当前原文页").count() == 0
            page.goto(f"{base_url}/#evidence=foreign", wait_until="networkidle")
            page.get_by_text("证据对应的解析修订已不存在").wait_for()
            assert page.locator(".evidence-snippet").count() == 0
            assert not writes, writes
            assert not errors, errors
            print("Playwright business evidence link passed: correct document/revision, frozen snippet, no writes")
        finally:
            browser.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18088")
