"""Playwright review-panel request failures and explicit recovery against offline Web assets."""

from __future__ import annotations

import json
import sys
import time

from playwright.sync_api import sync_playwright


def main(base_url: str) -> None:
    now = int(time.time() * 1000)
    document = {
        "id": "a" * 32, "original_name": "notice.pdf", "sha256": "b" * 64,
        "size": 16, "created_at_ms": now, "template_code": "official_document", "template_version": 1,
    }
    task = {
        "id": "c" * 32, "document_id": document["id"], "requested_tier": "basic", "actual_tier": "basic",
        "status": "done", "error_code": None, "created_at_ms": now, "updated_at_ms": now,
    }
    revision = {
        "id": "rev-1", "document_id": document["id"], "short_id": document["sha256"][:7],
        "tier": "basic", "page_range": "1", "producer_version": "4.0.6", "model_ref": None,
        "created_at_ms": now,
    }
    run = {
        "id": "run-1", "revision_id": revision["id"], "template_code": "official_document",
        "template_version": 1, "status": "done", "error_code": None,
        "created_at_ms": now, "updated_at_ms": now,
    }
    failures = {"revisions": True, "extractions": True, "run": True, "evidence": False}

    def route_api(route: object) -> None:
        path = route.request.url.split("/api/business", 1)[1].split("?", 1)[0]
        if path == f"/documents/{document['id']}/revisions" and failures["revisions"]:
            route.fulfill(status=503, content_type="application/json", body='{"detail":"revisions_unavailable"}')
            return
        if path == "/revisions/rev-1/extractions" and failures["extractions"]:
            route.fulfill(status=503, content_type="application/json", body='{"detail":"runs_unavailable"}')
            return
        if path == "/extractions/run-1" and failures["run"]:
            route.fulfill(status=503, content_type="application/json", body='{"detail":"run_unavailable"}')
            return
        if path == "/revisions/rev-1/evidence" and failures["evidence"]:
            route.fulfill(status=503, content_type="application/json", body='{"detail":"evidence_unavailable"}')
            return
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
        elif path == "/revisions/rev-1/extractions":
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
        elif path == f"/documents/{document['id']}/source":
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
            page.get_by_role("button", name="查看 notice.pdf，已解析").click()
            page.get_by_role("button", name="重试读取修订记录").wait_for()
            assert page.get_by_text("修订记录不可用：revisions_unavailable").count() == 1
            assert page.locator("#workbench").is_hidden()

            failures["revisions"] = False
            page.get_by_role("button", name="重试读取修订记录").click()
            page.get_by_role("button", name="重试读取解析修订").wait_for()
            assert page.get_by_role("button", name="生成字段候选").count() == 0
            assert page.get_by_text("解析修订读取失败：runs_unavailable").count() == 1

            failures["extractions"] = False
            page.get_by_role("button", name="重试读取解析修订").click()
            page.get_by_role("button", name="重试读取提取运行").wait_for()
            assert page.get_by_text("复核数据读取失败：run_unavailable").count() == 1
            assert page.get_by_text("正在读取提取运行…").count() == 0

            failures["run"] = False
            run["status"] = "queued"
            page.get_by_role("button", name="重试读取提取运行").click()
            page.get_by_text("字段提取正在后台执行", exact=False).wait_for()
            assert page.get_by_role("button", name="重试读取提取运行").count() == 0
            assert page.locator("#workbench .error-banner").count() == 0
            failures["evidence"] = True
            run["status"] = "done"
            page.get_by_text("证据列表读取失败：evidence_unavailable").wait_for(timeout=10000)
            assert page.get_by_text("冻结证据列表尚未成功读取；证据及字段关联状态未知。").count() == 1
            assert page.get_by_role("heading", name="字段候选与人工决定").count() == 0
            page.get_by_role("button", name="重试读取证据列表").click()
            page.get_by_text("证据列表读取失败：evidence_unavailable").wait_for()
            failures["evidence"] = False
            page.get_by_role("button", name="重试读取证据列表").click()
            page.get_by_text("候选字段", exact=False).wait_for()
            assert page.get_by_text("证据列表读取失败：evidence_unavailable").count() == 0
            assert not errors, errors
            print("Playwright review recovery passed: revision list, parse revision, extraction run and evidence retries")
        finally:
            browser.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18088")
