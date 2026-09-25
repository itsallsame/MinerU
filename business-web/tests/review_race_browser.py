"""A delayed review write must not refresh a different document's run."""

from __future__ import annotations

import json
import sys
import time

from playwright.sync_api import sync_playwright


def main(base_url: str) -> None:
    now = int(time.time() * 1000)
    documents = [
        {"id": char * 32, "original_name": name, "sha256": char * 64, "size": 16,
         "created_at_ms": now, "template_code": "official_document", "template_version": 1}
        for char, name in [("a", "first.pdf"), ("b", "second.pdf")]
    ]
    tasks = [
        {"id": char * 32, "document_id": document["id"], "requested_tier": "basic",
         "actual_tier": "basic", "status": "done", "error_code": None,
         "created_at_ms": now, "updated_at_ms": now}
        for char, document in [("c", documents[0]), ("d", documents[1])]
    ]
    revisions = [
        {"id": f"rev-{index}", "document_id": document["id"], "short_id": document["sha256"][:7],
         "tier": "basic", "page_range": "1", "producer_version": "4.0.6", "model_ref": None,
         "created_at_ms": now}
        for index, document in enumerate(documents, 1)
    ]
    runs = [
        {"id": f"run-{index}", "revision_id": revision["id"], "template_code": "official_document",
         "template_version": 1, "status": "done", "error_code": None,
         "created_at_ms": now, "updated_at_ms": now}
        for index, revision in enumerate(revisions, 1)
    ]
    calls: list[str] = []
    writes: list[str] = []
    decisions: list[dict[str, object]] = []
    issue = {"id": "issue-1", "run_id": "run-1", "field_code": "title", "code": "conflicting_candidates",
             "severity": "blocking", "status": "open", "created_at_ms": now}

    def route_api(route: object) -> None:
        path = route.request.url.split("/api/business", 1)[1].split("?", 1)[0]
        calls.append(path)
        if path == "/capabilities":
            payload = {"max_upload_bytes": 1000000, "parseable_extensions": ["pdf"],
                       "tiered_extensions": ["pdf"], "flash_only_extensions": [],
                       "tiers": ["flash", "basic", "standard", "advanced"]}
        elif path == "/templates":
            payload = []
        elif path == "/documents":
            payload = {"items": [{"document": doc, "task": task} for doc, task in zip(documents, tasks)],
                       "total": 2, "limit": 20, "offset": 0}
        elif path == "/templates/official_document":
            payload = {"code": "official_document", "name": "公文", "version": 1,
                       "built_in": True, "enabled": True, "created_at_ms": now,
                       "fields": [{"code": "title", "label": "标题", "type": "text", "required": True}]}
        elif path == "/extractions/run-1/fields/title/decisions":
            writes.append(path)
            payload = {"id": "decision-1", "run_id": "run-1", "field_code": "title", "value": "标题 1",
                       "evidence_id": "evidence-1", "basis": "candidate_acceptance", "created_at_ms": now}
            decisions.append(payload)
        elif path == "/issues/issue-1/resolutions":
            writes.append(path)
            issue["status"] = "resolved"
            payload = {"id": "resolution-1", "issue_id": "issue-1", "run_id": "run-1", "status": "resolved"}
        elif path == "/extractions/run-1/confirm":
            writes.append(path)
            payload = {"id": "result-1", "run_id": "run-1", "version": 1}
        elif path == "/revisions/rev-1/evidence" and route.request.method == "POST":
            writes.append(path)
            payload = {"id": "evidence-new"}
        elif path == "/revisions/rev-1/extractions" and route.request.method == "POST":
            writes.append(path)
            payload = {"id": "run-new"}
        elif path.endswith("/source"):
            route.fulfill(status=200, content_type="application/pdf", body=b"%PDF-1.4\n")
            return
        else:
            payload = None
            for index, (document, revision, run) in enumerate(zip(documents, revisions, runs), 1):
                if path == f"/documents/{document['id']}/revisions":
                    payload = [revision]
                elif path == f"/revisions/rev-{index}/extractions":
                    payload = [run]
                elif path == f"/revisions/rev-{index}/evidence":
                    payload = [{"id": f"evidence-{index}", "revision_id": revision["id"],
                                "document_id": document["id"], "locator": f"doc:{document['sha256'][:7]}/tier:basic/page:1",
                                "page_no": 1, "block_no": None, "bbox": None, "snippet": "标题原文",
                                "snippet_sha256": "e" * 64, "created_at_ms": now}]
                elif path == f"/extractions/run-{index}":
                    payload = {"run": run, "candidates": [{"id": f"candidate-{index}",
                               "run_id": run["id"], "field_code": "title", "value": f"标题 {index}",
                               "evidence_id": f"evidence-{index}", "method": "label_rule", "created_at_ms": now}],
                               "issues": [issue] if index == 1 else []}
                elif path in (f"/extractions/run-{index}/decisions", f"/extractions/run-{index}/results",
                              f"/extractions/run-{index}/audit"):
                    payload = decisions if path == "/extractions/run-1/decisions" else []
            if payload is None:
                raise AssertionError(f"Unexpected API path: {path}")
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.add_init_script("""
                const originalFetch = window.fetch.bind(window);
                window.fetch = (input, options = {}) => {
                  if (window.__delayFragment && String(input).includes(window.__delayFragment)) {
                    window.__delayFragment = null;
                    window.__pendingWrite = true;
                    return new Promise((resolve, reject) => {
                      window.__releaseWrite = () => originalFetch(input, options).then((response) => {
                        window.__writeSettled = true;
                        resolve(response);
                      }, reject);
                    });
                  }
                  return originalFetch(input, options);
                };
            """)
            page.route("**/api/business/**", route_api)
            page.goto(base_url, wait_until="networkidle")
            page.get_by_role("button", name="查看 first.pdf，已解析").click()
            page.get_by_text("标题 1").wait_for()
            page.evaluate("window.__delayFragment = '/extractions/run-1/fields/title/decisions'")
            page.get_by_role("button", name="接受候选").click()
            page.wait_for_function("window.__pendingWrite === true")
            page.get_by_role("button", name="查看 second.pdf，已解析").click()
            page.get_by_text("标题 2").wait_for()
            before = calls.count("/extractions/run-2")
            page.evaluate("window.__releaseWrite()")
            page.wait_for_function("window.__writeSettled === true")
            page.wait_for_timeout(100)
            assert calls.count("/extractions/run-2") == before, "Old write refreshed the new document"
            assert page.locator("#detail-content h2").inner_text() == "second.pdf"
            assert page.get_by_text("标题 2").count() == 1

            page.get_by_role("button", name="查看 first.pdf，已解析").click()
            page.get_by_text("已复核：标题 1").wait_for()
            page.get_by_label("多个候选值冲突处理原因").fill("已核验")
            page.evaluate(
                "window.__pendingWrite = false; window.__writeSettled = false;"
                "window.__delayFragment = '/issues/issue-1/resolutions'"
            )
            page.get_by_role("button", name="标记已解决").click()
            page.wait_for_function("window.__pendingWrite === true")
            page.get_by_role("button", name="查看 second.pdf，已解析").click()
            page.get_by_text("标题 2").wait_for()
            before = calls.count("/extractions/run-2")
            page.evaluate("window.__releaseWrite()")
            page.wait_for_function("window.__writeSettled === true")
            page.wait_for_timeout(100)
            assert calls.count("/extractions/run-2") == before
            assert page.locator("#detail-content h2").inner_text() == "second.pdf"

            page.get_by_role("button", name="查看 first.pdf，已解析").click()
            page.get_by_role("button", name="确认并生成不可变成果版本").wait_for(state="visible")
            assert page.get_by_role("button", name="确认并生成不可变成果版本").is_enabled()
            page.evaluate(
                "window.__pendingWrite = false; window.__writeSettled = false;"
                "window.__delayFragment = '/extractions/run-1/confirm'"
            )
            page.get_by_role("button", name="确认并生成不可变成果版本").click()
            page.wait_for_function("window.__pendingWrite === true")
            page.get_by_role("button", name="查看 second.pdf，已解析").click()
            page.get_by_text("标题 2").wait_for()
            before = calls.count("/extractions/run-2")
            page.evaluate("window.__releaseWrite()")
            page.wait_for_function("window.__writeSettled === true")
            page.wait_for_timeout(100)
            assert calls.count("/extractions/run-2") == before
            assert page.locator("#detail-content h2").inner_text() == "second.pdf"

            page.get_by_role("button", name="查看 first.pdf，已解析").click()
            page.get_by_role("button", name="采集第 N 页").wait_for()
            page.evaluate(
                "window.__pendingWrite = false; window.__writeSettled = false;"
                "window.__delayFragment = '/revisions/rev-1/evidence'"
            )
            page.get_by_role("button", name="采集第 N 页").click()
            page.wait_for_function("window.__pendingWrite === true")
            page.get_by_role("button", name="查看 second.pdf，已解析").click()
            page.get_by_text("标题 2").wait_for()
            before = calls.count("/extractions/run-2")
            page.evaluate("window.__releaseWrite()")
            page.wait_for_function("window.__writeSettled === true")
            page.wait_for_timeout(100)
            assert calls.count("/extractions/run-2") == before
            assert page.locator("#detail-content h2").inner_text() == "second.pdf"

            page.get_by_role("button", name="查看 first.pdf，已解析").click()
            page.get_by_role("button", name="重新生成字段候选").wait_for()
            page.evaluate(
                "window.__pendingWrite = false; window.__writeSettled = false;"
                "window.__delayFragment = '/revisions/rev-1/extractions'"
            )
            page.get_by_role("button", name="重新生成字段候选").click()
            page.wait_for_function("window.__pendingWrite === true")
            page.get_by_role("button", name="查看 second.pdf，已解析").click()
            page.get_by_text("标题 2").wait_for()
            before = calls.count("/extractions/run-2")
            page.evaluate("window.__releaseWrite()")
            page.wait_for_function("window.__writeSettled === true")
            page.wait_for_timeout(100)
            assert calls.count("/extractions/run-2") == before
            assert page.locator("#detail-content h2").inner_text() == "second.pdf"
            assert writes == [
                "/extractions/run-1/fields/title/decisions", "/issues/issue-1/resolutions",
                "/extractions/run-1/confirm", "/revisions/rev-1/evidence", "/revisions/rev-1/extractions",
            ]
            assert not errors, errors
            print("Playwright review races passed: delayed decision, issue, confirmation, evidence and extraction stay scoped")
        finally:
            browser.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18088")
