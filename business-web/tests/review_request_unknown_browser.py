"""Review decisions, issue resolutions and confirmations recover by the original request key."""

from __future__ import annotations

import json
import sys
import time

from playwright.sync_api import sync_playwright


def main(base_url: str) -> None:
    now = int(time.time() * 1000)
    document = {"id": "a" * 32, "original_name": "review.pdf", "sha256": "b" * 64,
                "size": 16, "created_at_ms": now, "template_code": "official_document", "template_version": 1}
    task = {"id": "c" * 32, "document_id": document["id"], "requested_tier": "basic", "actual_tier": "basic",
            "status": "done", "error_code": None, "created_at_ms": now, "updated_at_ms": now}
    revision = {"id": "rev-1", "document_id": document["id"], "short_id": document["sha256"][:7],
                "tier": "basic", "page_range": "1", "producer_version": "4.0.6", "model_ref": None,
                "created_at_ms": now}
    run = {"id": "run-1", "revision_id": revision["id"], "template_code": "official_document",
           "template_version": 1, "status": "done", "error_code": None, "created_at_ms": now, "updated_at_ms": now}
    evidence = {"id": "evidence-1", "revision_id": revision["id"], "document_id": document["id"],
                "locator": f"doc:{document['sha256'][:7]}/tier:basic/page:1", "page_no": 1,
                "block_no": None, "bbox": None, "snippet": "标题：年度通知", "snippet_sha256": "d" * 64,
                "created_at_ms": now}
    candidate = {"id": "candidate-1", "run_id": run["id"], "field_code": "title", "value": "年度通知",
                 "evidence_id": evidence["id"], "method": "label_rule", "created_at_ms": now}
    issue = {"id": "issue-1", "run_id": run["id"], "field_code": "title", "code": "conflicting_candidates",
             "severity": "blocking", "status": "open", "created_at_ms": now}
    decisions: list[dict[str, object]] = []
    resolutions: list[dict[str, object]] = []
    results: list[dict[str, object]] = []
    receipts: dict[str, dict[str, object]] = {}
    posts: list[tuple[str, str]] = []
    lookup_mode = {"resolution": "unavailable", "confirmation": "missing"}

    def reply(route: object, payload: object, status: int = 200) -> None:
        route.fulfill(status=status, content_type="application/json", body=json.dumps(payload, ensure_ascii=False))

    def api(route: object) -> None:
        request = route.request
        method = request.method
        path = request.url.split("/api/business", 1)[1].split("?", 1)[0]
        if method == "POST" and path in (
            "/extractions/run-1/fields/title/decisions", "/issues/issue-1/resolutions", "/extractions/run-1/confirm",
        ):
            key = request.headers["idempotency-key"]
            posts.append((path, key))
            if path.endswith("/decisions"):
                action, target = "decision", "run-1/title"
                if key not in receipts:
                    decisions.append({"id": "decision-1", "run_id": "run-1", "field_code": "title",
                                      "previous_value": None, "value": "年度通知", "evidence_id": evidence["id"],
                                      "basis": "candidate_acceptance", "source": "web", "reason": None,
                                      "created_at_ms": now})
                    receipts[key] = {"action": action, "target_id": target, "result": decisions[0]}
            elif path.endswith("/resolutions"):
                action, target = "resolution", issue["id"]
                if key not in receipts:
                    issue["status"] = "resolved"
                    resolutions.append({"id": "resolution-1", "issue_id": issue["id"], "run_id": "run-1",
                                        "previous_status": "open", "status": "resolved", "source": "web",
                                        "reason": "已核对原文", "created_at_ms": now})
                    receipts[key] = {"action": action, "target_id": target, "result": resolutions[0]}
            else:
                action, target = "confirmation", "run-1"
                if key not in receipts:
                    results.append({"id": "result-1", "run_id": "run-1", "version": 1,
                                    "revision_id": "rev-1", "template_code": "official_document", "template_version": 1,
                                    "fields": [{"field_code": "title", "value": "年度通知", "evidence_id": evidence["id"],
                                                "decision_id": "decision-1", "basis": "candidate_acceptance"}],
                                    "fields_sha256": "f" * 64, "source": "web", "created_at_ms": now})
                    receipts[key] = {"action": action, "target_id": target, "result": results[0]}
                else:
                    reply(route, receipts[key]["result"], 201)
                    return
            reply(route, {"detail": "response_lost"}, 503)
            return
        if path.startswith("/review-requests/"):
            key = path.rsplit("/", 1)[1]
            assert method == "GET"
            receipt = receipts.get(key)
            if not receipt:
                reply(route, {"detail": "not_found"}, 404)
            elif lookup_mode.get(receipt["action"]) == "unavailable":
                reply(route, {"detail": "lookup_unavailable"}, 503)
            elif lookup_mode.get(receipt["action"]) == "missing":
                reply(route, {"detail": "not_recorded_at_lookup"}, 404)
            else:
                reply(route, receipt)
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
            payload = {"run": run, "candidates": [candidate], "issues": [issue]}
        elif path == "/templates/official_document":
            payload = {"code": "official_document", "name": "公文", "version": 1, "built_in": True,
                       "enabled": True, "created_at_ms": now,
                       "fields": [{"code": "title", "label": "标题", "type": "text", "required": True}]}
        elif path == "/extractions/run-1/decisions":
            payload = decisions
        elif path == "/extractions/run-1/resolutions":
            payload = resolutions
        elif path == "/extractions/run-1/results":
            payload = results
        elif path == "/extractions/run-1/audit":
            payload = []
        elif path == f"/documents/{document['id']}/source":
            route.fulfill(status=200, content_type="application/pdf", body=b"%PDF-1.4\n")
            return
        else:
            raise AssertionError(f"Unexpected API call: {method} {path}")
        reply(route, payload)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/business/**", api)
            page.goto(base_url, wait_until="networkidle")
            page.get_by_role("button", name="查看 review.pdf，已解析").click()
            page.get_by_role("button", name="接受候选").click()
            page.get_by_text("已复核：年度通知", exact=False).wait_for()
            assert len(decisions) == 1 and len(posts) == 1
            assert page.evaluate("JSON.parse(localStorage.getItem('mineru.business.pendingReviewWrites.v1'))") == []

            card = page.locator('.issue-card[data-issue-id="issue-1"]')
            card.get_by_label("多个候选值冲突处理原因").fill("已核对原文")
            card.get_by_role("button", name="标记已解决").click()
            page.get_by_role("button", name="按原请求键核对复核写入").wait_for()
            assert len(resolutions) == 1 and len(posts) == 2
            saved = page.evaluate("JSON.parse(localStorage.getItem('mineru.business.pendingReviewWrites.v1'))")
            assert len(saved) == 1 and saved[0]["action"] == "resolution"
            assert saved[0]["requestKey"] == posts[-1][1]
            page.reload(wait_until="networkidle")
            page.get_by_role("button", name="查看 review.pdf，已解析").click()
            page.get_by_role("button", name="按原请求键核对复核写入").wait_for()
            lookup_mode["resolution"] = "accepted"
            page.get_by_role("button", name="按原请求键核对复核写入").click()
            page.locator('.issue-card[data-issue-id="issue-1"]').get_by_text("必核 · 已处理").wait_for()
            assert len(posts) == 2

            page.get_by_role("button", name="确认并生成不可变成果版本").click()
            page.get_by_role("button", name="用原键重试复核写入").wait_for()
            saved = page.evaluate("JSON.parse(localStorage.getItem('mineru.business.pendingReviewWrites.v1'))")
            assert len(saved) == 1 and saved[0]["action"] == "confirmation"
            assert len(results) == 1 and len(posts) == 3
            page.once("dialog", lambda dialog: dialog.accept())
            page.get_by_role("button", name="用原键重试复核写入").click()
            page.get_by_text("确认成果 v1").wait_for()
            assert len(posts) == 4 and posts[2][1] == posts[3][1] and len(results) == 1
            assert page.evaluate("JSON.parse(localStorage.getItem('mineru.business.pendingReviewWrites.v1'))") == []
            page.get_by_label("标题的复核值").fill("修订后标题")
            page.get_by_label("标题的证据").select_option(evidence["id"])
            page.get_by_label("标题的修订原因").fill("再次复核")
            page.evaluate("""() => {
              const write = Storage.prototype.setItem;
              Storage.prototype.setItem = function (key, value) {
                if (key === 'mineru.business.pendingReviewWrites.v1') throw new Error('storage_blocked');
                return write.call(this, key, value);
              };
            }""")
            page.get_by_role("button", name="保存复核决定").click()
            page.get_by_text("无法保存待核对的复核请求", exact=False).wait_for()
            assert len(posts) == 4, "storage failure must prevent an untracked review POST"
            assert not errors, errors
            print("Playwright keyed review-write recovery passed")
        finally:
            browser.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18088")
