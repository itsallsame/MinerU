"""A lost manual-extraction response must keep recovery read-only."""

from __future__ import annotations

import json
import sys
import time

from playwright.sync_api import sync_playwright


def main(base_url: str) -> None:
    now = int(time.time() * 1000)
    document = {"id": "a" * 32, "original_name": "extract.pdf", "sha256": "b" * 64,
                "size": 16, "created_at_ms": now, "template_code": "official_document", "template_version": 1}
    task = {"id": "c" * 32, "document_id": document["id"], "requested_tier": "basic",
            "actual_tier": "basic", "status": "done", "error_code": None,
            "created_at_ms": now, "updated_at_ms": now}
    revision = {"id": "rev-1", "document_id": document["id"], "short_id": document["sha256"][:7],
                "tier": "basic", "page_range": "1", "producer_version": "4.0.6", "model_ref": None,
                "created_at_ms": now}
    run = {"id": "run-1", "revision_id": revision["id"], "template_code": "official_document",
           "template_version": 1, "status": "done", "error_code": None,
           "created_at_ms": now, "updated_at_ms": now}
    calls = {"extract": 0, "runs": 0}
    reads_available = {"value": True}

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
            payload = [revision]
        elif path == f"/documents/{document['id']}/source":
            route.fulfill(status=200, content_type="application/pdf", body=b"%PDF-1.4\n")
            return
        elif path == "/revisions/rev-1/extractions" and request.method == "POST":
            calls["extract"] += 1
            reads_available["value"] = False
            route.fulfill(status=503, content_type="application/json", body='{"detail":"response_lost"}')
            return
        elif path == "/revisions/rev-1/extractions":
            calls["runs"] += 1
            if not reads_available["value"]:
                route.fulfill(status=503, content_type="application/json", body='{"detail":"runs_unavailable"}')
                return
            payload = [run]
        elif path == "/revisions/rev-1/evidence":
            payload = []
        elif path == "/extractions/run-1":
            payload = {"run": run, "candidates": [], "issues": []}
        elif path in ("/extractions/run-1/decisions", "/extractions/run-1/results", "/extractions/run-1/audit"):
            payload = []
        elif path == "/templates/official_document":
            payload = {"code": "official_document", "name": "公文", "version": 1,
                       "built_in": True, "enabled": True, "created_at_ms": now, "fields": []}
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
            page.get_by_role("button", name="查看 extract.pdf，已解析").click()
            page.get_by_role("button", name="重新生成字段候选").click()
            page.get_by_text("字段提取提交结果未确认", exact=False).wait_for()
            check = page.get_by_role("button", name="核对提取运行")
            assert calls["extract"] == 1
            reads_available["value"] = True
            check.click()
            page.get_by_text("上次字段提取提交结果未确认", exact=False).wait_for()
            assert calls["extract"] == 1, "read-only extraction check repeated POST"
            assert calls["runs"] >= 3
            page.get_by_role("button", name="查看 extract.pdf，已解析").click()
            page.get_by_role("button", name="再次生成字段候选（上次结果未确认）").wait_for()
            assert calls["extract"] == 1, "reopening the document repeated extraction POST"
            assert not errors, errors
            print("Playwright manual-extraction unknown-result recovery passed")
        finally:
            browser.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18088")
