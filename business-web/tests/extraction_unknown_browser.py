"""A lost manual-extraction response remains bound to its key across page reload."""

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
    replay_run = {**run, "id": "run-2", "created_at_ms": now + 1, "updated_at_ms": now + 1}
    calls = {"extract": 0, "runs": 0, "request": 0}
    request_keys: list[str] = []
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
            request_keys.append(request.headers["idempotency-key"])
            if calls["extract"] == 3:
                route.fulfill(status=202, content_type="application/json", body=json.dumps(replay_run))
                return
            if calls["extract"] == 1:
                reads_available["value"] = False
            route.fulfill(status=503, content_type="application/json", body='{"detail":"response_lost"}')
            return
        elif path.startswith("/extraction-requests/"):
            calls["request"] += 1
            if path == f"/extraction-requests/{request_keys[0]}":
                if not reads_available["value"]:
                    route.fulfill(status=503, content_type="application/json", body='{"detail":"lookup_unavailable"}')
                    return
                payload = run
            else:
                assert path == f"/extraction-requests/{request_keys[1]}"
                route.fulfill(status=404, content_type="application/json", body='{"detail":"not_recorded"}')
                return
        elif path == "/revisions/rev-1/extractions":
            calls["runs"] += 1
            if not reads_available["value"]:
                route.fulfill(status=503, content_type="application/json", body='{"detail":"runs_unavailable"}')
                return
            payload = [replay_run, run] if calls["extract"] >= 3 else [run]
        elif path == "/revisions/rev-1/evidence":
            payload = []
        elif path == "/extractions/run-1":
            payload = {"run": run, "candidates": [], "issues": []}
        elif path == "/extractions/run-2":
            payload = {"run": replay_run, "candidates": [], "issues": []}
        elif path in ("/extractions/run-1/decisions", "/extractions/run-1/results", "/extractions/run-1/audit",
                      "/extractions/run-2/decisions", "/extractions/run-2/results", "/extractions/run-2/audit"):
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
            page.get_by_text("原请求键核对暂不可用", exact=False).wait_for()
            check = page.get_by_role("button", name="按原请求键核对提取")
            assert calls["extract"] == 1
            assert len(request_keys[0]) == 32
            saved = page.evaluate("JSON.parse(localStorage.getItem('mineru.business.pendingExtractions.v1'))")
            assert saved == [{"revisionId": "rev-1", "requestKey": request_keys[0]}]
            page.reload(wait_until="networkidle")
            page.get_by_role("button", name="查看 extract.pdf，已解析").click()
            check = page.get_by_role("button", name="按原请求键核对提取")
            assert calls["extract"] == 1, "reload repeated the extraction POST"
            reads_available["value"] = True
            check.click()
            page.get_by_text("已按原请求键确认提取运行", exact=False).wait_for()
            assert calls["extract"] == 1, "read-only extraction check repeated POST"
            assert calls["request"] == 2
            assert page.evaluate("JSON.parse(localStorage.getItem('mineru.business.pendingExtractions.v1'))") == []
            page.get_by_role("button", name="重新生成字段候选").wait_for()
            page.get_by_role("button", name="重新生成字段候选").click()
            page.get_by_text("原请求键暂未查到", exact=False).wait_for()
            assert request_keys[1] != request_keys[0]
            saved = page.evaluate("JSON.parse(localStorage.getItem('mineru.business.pendingExtractions.v1'))")
            assert saved == [{"revisionId": "rev-1", "requestKey": request_keys[1]}]
            page.reload(wait_until="networkidle")
            page.get_by_role("button", name="查看 extract.pdf，已解析").click()
            page.once("dialog", lambda dialog: dialog.accept())
            page.get_by_role("button", name="用原键重试提取").click()
            page.get_by_role("button", name="重新生成字段候选").wait_for()
            assert calls["extract"] == 3 and request_keys[2] == request_keys[1]
            assert page.evaluate("JSON.parse(localStorage.getItem('mineru.business.pendingExtractions.v1'))") == []
            assert not errors, errors
            print("Playwright keyed manual-extraction recovery across reload passed")
        finally:
            browser.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18088")
