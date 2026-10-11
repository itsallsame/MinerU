"""Selecting a parsed document immediately shows its paged parsing result."""

from __future__ import annotations

import json
import sys
import time
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright


def main(base_url: str) -> None:
    now = int(time.time() * 1000)
    document = {"id": "a" * 32, "original_name": "example.pdf", "sha256": "b" * 64,
                "size": 16, "created_at_ms": now, "template_code": None, "template_version": None}
    task = {"id": "c" * 32, "document_id": document["id"], "requested_tier": "basic",
            "actual_tier": "basic", "status": "done", "error_code": None,
            "created_at_ms": now, "updated_at_ms": now}
    revision = {"id": "rev-1", "document_id": document["id"], "short_id": "bbbbbbb",
                "tier": "basic", "page_range": "1-2", "producer_version": "4.0.6",
                "model_ref": None, "created_at_ms": now}
    reads: list[str] = []
    fail_next = {"value": False}

    def api(route: object) -> None:
        parsed = urlparse(route.request.url)
        path = parsed.path.split("/api/business", 1)[1]
        if path == "/capabilities":
            payload = {"max_upload_bytes": 1000000, "parseable_extensions": ["pdf"],
                       "tiered_extensions": ["pdf"], "flash_only_extensions": [],
                       "tiers": ["flash", "basic", "standard", "advanced"]}
        elif path == "/templates":
            payload = []
        elif path == "/documents":
            payload = {"items": [{"document": document, "task": task}], "total": 1,
                       "limit": 20, "offset": 0}
        elif path == f"/documents/{document['id']}/revisions":
            payload = [revision]
        elif path in ("/revisions/rev-1/extractions", "/revisions/rev-1/evidence"):
            payload = []
        elif path == f"/documents/{document['id']}/source":
            route.fulfill(status=200, content_type="application/pdf", body=b"%PDF-1.4\n")
            return
        elif path == "/revisions/rev-1/content":
            locator = parse_qs(parsed.query)["locator"][0]
            reads.append(locator)
            if fail_next["value"]:
                fail_next["value"] = False
                route.fulfill(status=503, content_type="application/json", body='{"detail":"temporarily_unavailable"}')
                return
            page_no = int(locator.rsplit("page:", 1)[1])
            payload = {"document_id": document["id"], "revision_id": revision["id"],
                       "locator": locator, "tier": "basic", "content": f"第 {page_no} 页解析正文",
                       "truncated": False, "next_locator": None,
                       "state": "historical_parse_unconfirmed"}
        else:
            raise AssertionError(f"Unexpected API call: {path}")
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload, ensure_ascii=False))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/business/**", api)
            page.goto(base_url, wait_until="networkidle")
            page.get_by_role("button", name="查看 example.pdf，已解析").click()
            result = page.locator("#parse-result")
            result.get_by_text("第 1 页解析正文").wait_for()
            assert reads == ["doc:bbbbbbb/tier:basic/page:1"]
            result.get_by_role("button", name="下一页 →").click()
            result.get_by_text("第 2 页解析正文").wait_for()
            assert reads[-1] == "doc:bbbbbbb/tier:basic/page:2"
            assert result.get_by_role("button", name="下一页 →").is_disabled()
            fail_next["value"] = True
            result.get_by_role("button", name="← 上一页").click()
            result.get_by_text("解析结果暂不可读：temporarily_unavailable").wait_for()
            result.get_by_role("button", name="重新读取这一页").click()
            result.get_by_text("第 1 页解析正文").wait_for()
            assert not errors, errors
            print("Playwright parse result passed: automatic display, pagination and retry")
        finally:
            browser.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18088")
