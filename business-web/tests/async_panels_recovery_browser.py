"""Search and audit failures retry the same request without losing prior pages."""

from __future__ import annotations

import json
import sys
import time
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright


def main(base_url: str) -> None:
    now = int(time.time() * 1000)
    document = {"id": "a" * 32, "original_name": "notice.html", "sha256": "b" * 64,
                "size": 100, "created_at_ms": now, "template_code": None, "template_version": None}
    counts = {"search": 0, "blocks_later": 0, "audit_first": 0, "audit_later": 0}
    search_queries: list[str] = []

    def respond(route: object, payload: object, status: int = 200) -> None:
        route.fulfill(status=status, content_type="application/json", body=json.dumps(payload, ensure_ascii=False))

    def audit_item(char: str, name: str) -> dict[str, object]:
        return {"event": {"id": char * 32, "run_id": "r" * 32, "action": "field_decided",
                          "source": "web", "old_value": None, "new_value": name,
                          "reason": None, "created_at_ms": now},
                "document_id": document["id"], "document_name": name, "revision_id": "rev-1"}

    def api(route: object) -> None:
        parsed = urlsplit(route.request.url)
        path = parsed.path.split("/api/business", 1)[1]
        query = parse_qs(parsed.query)
        if path == "/capabilities":
            respond(route, {"max_upload_bytes": 1000000, "parseable_extensions": ["html"],
                            "tiered_extensions": [], "flash_only_extensions": ["html"],
                            "tiers": ["flash", "basic", "standard", "advanced"]})
        elif path == "/templates":
            respond(route, [])
        elif path == "/documents":
            respond(route, {"items": [], "total": 0, "limit": 20, "offset": 0})
        elif path == "/search":
            counts["search"] += 1
            search_queries.append(query["query"][0])
            if counts["search"] == 1:
                respond(route, {"detail": "search_unavailable"}, 503)
            else:
                respond(route, {"items": [{"document": document, "revision_id": "rev-1",
                                          "tier": "flash", "snippet": "年度通知摘要"}],
                                "scan_complete": True, "scanned_doclib_hits": 1})
        elif path == "/revisions/rev-1/search-blocks":
            later = "start_page" in query
            if later:
                counts["blocks_later"] += 1
            if later and counts["blocks_later"] == 1:
                respond(route, {"detail": "blocks_unavailable"}, 503)
            else:
                page = 2 if later else 1
                respond(route, {"revision_id": "rev-1", "scanned_pages": 1,
                                "next_page": None if later else 2,
                                "items": [{"locator": f"doc:bbbbbbb/tier:flash/page:{page}/block:1",
                                           "page_no": page, "block_no": 1, "bbox": None,
                                           "snippet": f"第{page}页原文", "state": "historical_parse_unconfirmed"}]})
        elif path == "/audit":
            later = "before" in query
            key = "audit_later" if later else "audit_first"
            counts[key] += 1
            if counts[key] == 1:
                respond(route, {"detail": "audit_unavailable"}, 503)
            else:
                respond(route, {"items": [audit_item("o" if later else "n", "旧记录" if later else "新记录")],
                                "next_before": None if later else "n" * 32})
        else:
            raise AssertionError(f"Unexpected API path: {path}")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/business/**", api)
            page.goto(base_url, wait_until="networkidle")

            page.get_by_label("检索已解析的业务文档").fill("年度通知")
            page.get_by_role("button", name="检索", exact=True).click()
            page.get_by_text("检索失败：search_unavailable").wait_for()
            page.get_by_label("检索已解析的业务文档").fill("另一关键词")
            page.get_by_role("button", name="重试本次检索").click()
            page.get_by_text("当前索引预览，未人工确认：年度通知摘要").wait_for()
            assert search_queries == ["年度通知", "年度通知"]
            page.get_by_role("button", name="查找块级候选依据").click()
            page.get_by_text("第1页原文").wait_for()
            page.get_by_role("button", name="继续扫描后续页").click()
            page.get_by_text("历史块检索失败：blocks_unavailable").wait_for()
            assert page.get_by_text("第1页原文").count() == 1
            page.get_by_role("button", name="重试扫描这一段").click()
            page.get_by_text("第2页原文").wait_for()
            assert page.get_by_text("第1页原文").count() == 1
            assert counts["blocks_later"] == 2

            # A continuation can finish after the user restarts this hit's scan.
            # The old page must not be appended to the new result set.
            page.evaluate("""() => {
              const originalFetch = window.fetch.bind(window);
              window.__blockContinuationPending = false;
              window.__delayOneContinuation = true;
              window.fetch = (input, options) => {
                if (window.__delayOneContinuation && String(input).includes('/search-blocks?')
                  && String(input).includes('start_page=2')) {
                  window.__delayOneContinuation = false;
                  return originalFetch(input, options).then((response) => new Promise((resolve) => {
                    window.__blockContinuationPending = true;
                    window.__releaseContinuation = () => resolve(response);
                  }));
                }
                return originalFetch(input, options);
              };
            }""")
            page.get_by_role("button", name="查找块级候选依据").click()
            page.get_by_role("button", name="继续扫描后续页").click()
            page.wait_for_function("window.__blockContinuationPending === true")
            page.get_by_role("button", name="查找块级候选依据").click()
            page.get_by_text("第1页原文").wait_for()
            assert page.get_by_text("第2页原文").count() == 0
            page.evaluate("window.__releaseContinuation()")
            page.wait_for_timeout(100)
            assert page.get_by_text("第1页原文").count() == 1
            assert page.get_by_text("第2页原文").count() == 0
            assert page.get_by_role("button", name="继续扫描后续页").count() == 1

            page.locator("#audit-panel > summary").click()
            page.get_by_text("审计记录不可用：audit_unavailable").wait_for()
            assert page.get_by_role("button", name="加载更早记录").is_hidden()
            page.get_by_role("button", name="重试读取审计记录").click()
            page.get_by_text("新记录 · 字段决定").wait_for()
            page.get_by_role("button", name="加载更早记录").click()
            page.get_by_text("已加载记录保留，后续页尚未读取").wait_for()
            assert page.locator(".audit-record").count() == 1
            page.get_by_role("button", name="重试加载这一页").click()
            page.get_by_text("旧记录 · 字段决定").wait_for()
            assert page.locator(".audit-record").count() == 2
            assert counts["audit_first"] == 2 and counts["audit_later"] == 2
            assert not errors, errors
            print("Playwright async panels recovery passed: exact-query, stale block continuation, audit page retries")
        finally:
            browser.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18088")
