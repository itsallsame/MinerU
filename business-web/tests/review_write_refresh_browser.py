"""Accepted review writes remain acknowledged when their follow-up read fails."""

from __future__ import annotations

import json
import sys
import time

from playwright.sync_api import sync_playwright


def main(base_url: str) -> None:
    now = int(time.time() * 1000)
    document = {"id": "a" * 32, "original_name": "notice.pdf", "sha256": "b" * 64,
                "size": 16, "created_at_ms": now, "template_code": "official_document", "template_version": 1}
    task = {"id": "c" * 32, "document_id": document["id"], "requested_tier": "basic", "actual_tier": "basic",
            "status": "done", "error_code": None, "created_at_ms": now, "updated_at_ms": now}
    revision = {"id": "rev-1", "document_id": document["id"], "short_id": document["sha256"][:7],
                "tier": "basic", "page_range": "1", "producer_version": "4.0.6", "model_ref": None,
                "created_at_ms": now}
    run = {"id": "run-1", "revision_id": revision["id"], "template_code": "official_document",
           "template_version": 1, "status": "done", "error_code": None, "created_at_ms": now,
           "updated_at_ms": now}
    evidence = {"id": "evidence-1", "revision_id": revision["id"], "document_id": document["id"],
                "locator": f"doc:{document['sha256'][:7]}/tier:basic/page:1", "page_no": 1,
                "block_no": None, "bbox": None, "snippet": "标题：年度通知", "snippet_sha256": "d" * 64,
                "created_at_ms": now}
    candidate = {"id": "candidate-1", "run_id": run["id"], "field_code": "title", "value": "年度通知",
                 "evidence_id": evidence["id"], "method": "label_rule", "created_at_ms": now}
    issues = [{"id": f"issue-{number}", "run_id": run["id"], "field_code": "title",
               "code": "conflicting_candidates", "severity": "blocking", "status": "open",
               "created_at_ms": now} for number in (1, 2)]
    decisions: list[dict[str, object]] = []
    writes: list[str] = []
    failures = {"decisions": False, "audit": False}

    def reply(route: object, payload: object, status: int = 200) -> None:
        route.fulfill(status=status, content_type="application/json", body=json.dumps(payload, ensure_ascii=False))

    def route_api(route: object) -> None:
        path = route.request.url.split("/api/business", 1)[1].split("?", 1)[0]
        method = route.request.method
        if method == "POST" and path == "/extractions/run-1/fields/title/decisions":
            writes.append("decision")
            body = json.loads(route.request.post_data)
            decision = {"id": f"decision-{len(decisions) + 1}", "run_id": run["id"],
                        "field_code": "title", "value": body["value"], "evidence_id": body["evidence_id"],
                        "basis": "candidate_acceptance" if not decisions else "manual_correction",
                        "source": "web", "reason": body["reason"], "created_at_ms": now + len(decisions)}
            decisions.append(decision)
            reply(route, decision, 201)
            return
        if method == "POST" and path in ("/issues/issue-1/resolutions", "/issues/issue-2/resolutions"):
            writes.append(path)
            body = json.loads(route.request.post_data)
            issue = next(item for item in issues if item["id"] == path.split("/")[2])
            issue["status"] = body["status"]
            reply(route, {"id": f"resolution-{len(writes)}", "issue_id": issue["id"],
                          "run_id": run["id"], "status": body["status"], "source": "web",
                          "reason": body["reason"], "created_at_ms": now}, 201)
            return
        if path == "/extractions/run-1/decisions" and failures["decisions"]:
            reply(route, {"detail": "decisions_unavailable"}, 503)
            return
        if path == "/extractions/run-1/audit" and failures["audit"]:
            reply(route, {"detail": "audit_unavailable"}, 503)
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
            payload = [evidence]
        elif path == "/extractions/run-1":
            payload = {"run": run, "candidates": [candidate], "issues": issues}
        elif path == "/templates/official_document":
            payload = {"code": "official_document", "name": "公文", "version": 1, "built_in": True,
                       "enabled": True, "created_at_ms": now,
                       "fields": [{"code": "title", "label": "标题", "type": "text", "required": True}]}
        elif path == "/extractions/run-1/decisions":
            payload = decisions
        elif path in ("/extractions/run-1/results", "/extractions/run-1/audit"):
            payload = []
        elif path == f"/documents/{document['id']}/source":
            route.fulfill(status=200, content_type="application/pdf", body=b"%PDF-1.4\n")
            return
        else:
            raise AssertionError(f"Unexpected API path: {method} {path}")
        reply(route, payload)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/business/**", route_api)
            page.goto(base_url, wait_until="networkidle")
            page.get_by_role("button", name="查看 notice.pdf，已解析").click()
            page.get_by_role("button", name="接受候选").wait_for()

            failures["decisions"] = True
            page.get_by_role("button", name="接受候选").click()
            page.get_by_text("复核决定已保存，但复核数据读取失败：decisions_unavailable").wait_for()
            assert writes.count("decision") == 1
            failures["decisions"] = False
            page.get_by_role("button", name="重试读取提取运行").click()
            page.get_by_text("已复核：年度通知", exact=False).wait_for()
            assert writes.count("decision") == 1

            failures["audit"] = True
            first = page.locator('.issue-card[data-issue-id="issue-1"]')
            first.get_by_label("多个候选值冲突处理原因").fill("与原文一致")
            first.get_by_role("button", name="标记已解决").click()
            page.get_by_text("问题处理已保存，但复核数据读取失败：audit_unavailable").wait_for()
            assert writes.count("/issues/issue-1/resolutions") == 1
            failures["audit"] = False
            page.get_by_role("button", name="重试读取提取运行").click()
            first = page.locator('.issue-card[data-issue-id="issue-1"]')
            first.get_by_text("必核 · 已处理").wait_for()
            assert writes.count("/issues/issue-1/resolutions") == 1

            failures["audit"] = True
            second = page.locator('.issue-card[data-issue-id="issue-2"]')
            second.get_by_label("多个候选值冲突处理原因").fill("经核验可忽略")
            second.get_by_role("button", name="有依据地忽略").click()
            page.get_by_text("问题处理已保存，但复核数据读取失败：audit_unavailable").wait_for()
            assert writes.count("/issues/issue-2/resolutions") == 1
            failures["audit"] = False
            page.get_by_role("button", name="重试读取提取运行").click()
            second = page.locator('.issue-card[data-issue-id="issue-2"]')
            second.get_by_text("必核 · 已处理").wait_for()
            assert writes.count("/issues/issue-2/resolutions") == 1

            failures["decisions"] = True
            page.get_by_label("标题的复核值").fill("修订后的标题")
            page.get_by_label("标题的证据").select_option(evidence["id"])
            page.get_by_label("标题的修订原因").fill("按原文修订")
            page.get_by_role("button", name="保存复核决定").click()
            page.get_by_text("复核决定已保存，但复核数据读取失败：decisions_unavailable").wait_for()
            assert writes.count("decision") == 2
            failures["decisions"] = False
            page.get_by_role("button", name="重试读取提取运行").click()
            page.get_by_text("已复核：修订后的标题", exact=False).wait_for()
            assert writes.count("decision") == 2
            assert not errors, errors
            print("Playwright review writes passed: accepted decisions, manual edits, resolved and ignored issues survive GET failure")
        finally:
            browser.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18088")
