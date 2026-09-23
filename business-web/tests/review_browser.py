"""Playwright review-state workflow against the real Web bundle and mocked business records.

Run with a live local business Web/API: python review_browser.py http://127.0.0.1:18088 [screenshot.png]
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


def main(base_url: str, screenshot: Path | None = None) -> None:
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
        "tier": "basic", "page_range": "1", "producer_version": "4.0.6",
        "model_ref": None, "created_at_ms": now,
    }
    run = {
        "id": "run-1", "revision_id": revision["id"], "template_code": "official_document", "template_version": 1,
        "status": "done", "error_code": None, "created_at_ms": now, "updated_at_ms": now,
    }
    candidate = {
        "id": "candidate-1", "run_id": run["id"], "field_code": "title", "value": "年度通知",
        "evidence_id": "evidence-1", "method": "label_rule", "created_at_ms": now,
    }
    evidence = {
        "id": "evidence-1", "revision_id": revision["id"], "document_id": document["id"],
        "locator": f"doc:{document['sha256'][:7]}/tier:basic/page:1", "page_no": 1, "block_no": None,
        "bbox": None, "snippet": "标题：年度通知", "snippet_sha256": "d" * 64, "created_at_ms": now,
    }
    issue = {
        "id": "issue-1", "run_id": run["id"], "field_code": "title", "code": "conflicting_candidates",
        "severity": "blocking", "status": "open", "created_at_ms": now,
    }
    decisions: list[dict[str, object]] = []
    results: list[dict[str, object]] = []
    writes: list[tuple[str, dict[str, object]]] = []
    navigation_status = "current_match"

    def fulfill(route: object, payload: object, status: int = 200) -> None:
        route.fulfill(status=status, content_type="application/json", body=json.dumps(payload, ensure_ascii=False))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900}, accept_downloads=True)
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/business/documents?*", lambda route: fulfill(route, {
                "items": [{"document": document, "task": task}], "total": 1, "limit": 20, "offset": 0,
            }))
            page.route("**/api/business/documents/*/revisions", lambda route: fulfill(route, [revision]))
            page.route("**/api/business/documents/*/source", lambda route: route.fulfill(
                status=200, content_type="application/pdf", body=b"%PDF-1.4\n",
            ))
            page.route("**/api/business/revisions/rev-1/extractions", lambda route: fulfill(route, [run]))
            page.route("**/api/business/revisions/rev-1/evidence", lambda route: fulfill(route, [evidence]))
            page.route("**/api/business/revisions/rev-1/outline", lambda route: fulfill(route, {
                "revision_id": "rev-1", "scanned_pages": 1, "next_page": None,
                "items": [{"level": 1, "title": "年度通知目录", "page_no": 1,
                           "locator": evidence["locator"], "state": "historical_parse_unconfirmed"}],
            }))
            page.route("**/api/business/revisions/rev-1/structure?*", lambda route: fulfill(route, {
                "document_id": document["id"], "revision_id": "rev-1", "page_no": 1,
                "blocks": [{"type": "doc_title", "block_no": 1, "locator": f"{evidence['locator']}/block:1",
                            "preview": "年度通知原生标题", "level": 1, "bbox": [1, 2, 3, 4],
                            "state": "historical_parse_unconfirmed"}],
            }))
            page.route("**/api/business/revisions/rev-1/content?*", lambda route: fulfill(route, {
                "document_id": document["id"], "revision_id": "rev-1", "locator": evidence["locator"],
                "tier": "basic", "content": "# 年度通知目录", "truncated": False, "next_locator": None,
                "state": "historical_parse_unconfirmed",
            }))
            page.route("**/api/business/evidence/evidence-1", lambda route: fulfill(route, {
                **evidence, "navigation_status": navigation_status,
            }))
            page.route("**/api/business/extractions/run-1", lambda route: fulfill(route, {
                "run": run, "candidates": [candidate], "issues": [issue],
            }))
            page.route("**/api/business/extractions/run-1/decisions", lambda route: fulfill(route, decisions))
            page.route("**/api/business/extractions/run-1/results", lambda route: fulfill(route, results))
            page.route("**/api/business/extractions/run-1/audit", lambda route: fulfill(route, []))
            page.route("**/api/business/templates/official_document?version=1", lambda route: fulfill(route, {
                "code": "official_document", "name": "公文", "version": 1, "built_in": True,
                "enabled": True, "created_at_ms": now,
                "fields": [{"code": "title", "label": "标题", "type": "text", "required": True}],
            }))

            def decide(route: object) -> None:
                payload = json.loads(route.request.post_data)
                writes.append(("decision", payload))
                assert payload == {
                    "value": "年度通知", "evidence_id": "evidence-1", "source": "web", "reason": None,
                }
                decision = {
                    "id": "decision-1", "run_id": run["id"], "field_code": "title", "previous_value": None,
                    "value": "年度通知", "evidence_id": evidence["id"], "basis": "candidate_acceptance",
                    "source": "web", "reason": None, "created_at_ms": now,
                }
                decisions.append(decision)
                fulfill(route, decision, 201)

            def resolve(route: object) -> None:
                payload = json.loads(route.request.post_data)
                writes.append(("resolution", payload))
                assert payload == {"status": "resolved", "source": "web", "reason": "与原文一致"}
                issue["status"] = "resolved"
                fulfill(route, {
                    "id": "resolution-1", "issue_id": issue["id"], "run_id": run["id"],
                    "previous_status": "open", "status": "resolved", "source": "web", "reason": payload["reason"],
                    "created_at_ms": now,
                }, 201)

            def confirm(route: object) -> None:
                payload = json.loads(route.request.post_data)
                writes.append(("confirmation", payload))
                assert payload == {"source": "web"}
                result = {
                    "id": "result-1", "run_id": run["id"], "version": 1, "revision_id": revision["id"],
                    "template_code": "official_document", "template_version": 1,
                    "fields": [{
                        "field_code": "title", "value": "年度通知", "evidence_id": evidence["id"],
                        "decision_id": "decision-1", "basis": "candidate_acceptance",
                    }],
                    "fields_sha256": "e" * 64, "source": "web", "created_at_ms": now,
                }
                results.insert(0, result)
                fulfill(route, result, 201)

            page.route("**/api/business/extractions/run-1/fields/title/decisions", decide)
            page.route("**/api/business/issues/issue-1/resolutions", resolve)
            page.route("**/api/business/extractions/run-1/confirm", confirm)

            page.goto(base_url, wait_until="networkidle")
            page.get_by_role("button", name="查看 notice.pdf，已解析").click()
            page.get_by_text("机器候选 · 未确认").wait_for()
            page.get_by_role("button", name="读取标题目录").click()
            page.get_by_text("年度通知目录 · 第 1 页").wait_for()
            page.get_by_role("button", name="读取所在页").click()
            page.get_by_text("# 年度通知目录").wait_for()
            structure = page.locator(".review-section").filter(has=page.get_by_role("heading", name="原生解析块结构"))
            structure.get_by_role("button", name="查看本页块").click()
            structure.get_by_text("年度通知原生标题").wait_for()
            assert structure.get_by_text("doc_title · 标题级别 1 · 块 1").count() == 1
            confirm_button = page.get_by_role("button", name="确认并生成不可变成果版本")
            assert confirm_button.is_disabled()
            assert page.get_by_text("必填字段「标题」尚未作出复核决定").count() == 1

            page.get_by_role("button", name="看证据").first.click()
            page.get_by_text("当前定位内容与冻结片段一致").wait_for()
            page.locator(".evidence-snippet").get_by_text("标题：年度通知").wait_for()
            page.get_by_role("button", name="尝试跳转当前原文页").click()
            assert page.locator("iframe.source-preview").get_attribute("src").endswith("#page=1")
            navigation_status = "changed"
            page.get_by_role("button", name="看证据").first.click()
            page.get_by_text("当前定位内容已变化").wait_for()
            assert page.get_by_role("button", name="尝试跳转当前原文页").count() == 0
            page.get_by_role("button", name="接受候选").first.click()
            page.get_by_text("已复核：年度通知").wait_for()
            assert confirm_button.is_disabled(), "An open issue must still block confirmation"
            page.get_by_label("多个候选值冲突处理原因").fill("与原文一致")
            page.get_by_role("button", name="标记已解决").click()
            page.get_by_text("必核 · 已处理").wait_for()
            assert confirm_button.is_enabled()
            if screenshot:
                page.screenshot(path=str(screenshot), full_page=True)
            confirm_button.click()
            page.get_by_text("确认成果 v1").wait_for()
            assert confirm_button.is_disabled(), "An unchanged result must not be reconfirmed"
            with page.expect_download() as download_info:
                page.get_by_role("button", name="下载 JSON").click()
            downloaded = json.loads(Path(download_info.value.path()).read_text())
            assert downloaded["fields"][0]["value"] == "年度通知"
            assert "candidates" not in downloaded
            with page.expect_download() as markdown_info:
                page.get_by_role("button", name="下载 Markdown").click()
            markdown = Path(markdown_info.value.path()).read_text()
            assert "标题：年度通知" in markdown and "evidence-1" in markdown
            assert "candidate-1" not in markdown
            assert [name for name, _payload in writes] == ["decision", "resolution", "confirmation"]
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            assert not errors, errors
            print("Playwright review workflow passed: evidence, explicit decision, issue gate, result version and download")
        finally:
            browser.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18088", Path(sys.argv[2]) if len(sys.argv) > 2 else None)
