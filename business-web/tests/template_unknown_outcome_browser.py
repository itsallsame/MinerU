"""Unknown template writes survive reload and never retry with a new request key."""

from __future__ import annotations

import json
import sys
import time

from playwright.sync_api import sync_playwright


def main(base_url: str) -> None:
    now = int(time.time() * 1000)
    template = {
        "code": "my_report", "name": "初版", "version": 1, "built_in": False, "enabled": True,
        "fields": [{"code": "title", "label": "标题", "type": "text", "required": False}],
        "created_at_ms": now,
    }
    receipts: dict[str, dict[str, object]] = {}
    writes: list[tuple[str, dict[str, object]]] = []
    state = {"mode": "accepted_error", "lookup_missing": True}

    def fulfill(route: object, payload: object, status: int = 200) -> None:
        route.fulfill(status=status, content_type="application/json", body=json.dumps(payload, ensure_ascii=False))

    def api(route: object) -> None:
        nonlocal template
        request = route.request
        path = request.url.split("/api/business", 1)[1].split("?", 1)[0]
        if path == "/capabilities":
            fulfill(route, {
                "max_upload_bytes": 1000000, "parseable_extensions": ["pdf"],
                "tiered_extensions": ["pdf"], "flash_only_extensions": [],
                "tiers": ["flash", "basic", "standard", "advanced"],
            })
        elif path == "/documents" and request.method == "GET":
            fulfill(route, {"items": [], "total": 0, "limit": 20, "offset": 0})
        elif path == "/templates" and request.method == "GET":
            fulfill(route, [template])
        elif path == "/templates/my_report" and request.method == "PUT":
            key = request.headers.get("idempotency-key", "")
            body = json.loads(request.post_data)
            writes.append((key, body))
            if state["mode"] == "accepted_error":
                template = {**template, **body, "version": template["version"] + 1}
                receipts[key] = template.copy()
                fulfill(route, {"detail": "response_lost"}, 503)
            elif state["mode"] == "not_accepted_error":
                fulfill(route, {"detail": "response_lost"}, 503)
            else:
                if key not in receipts:
                    template = {**template, **body, "version": template["version"] + 1}
                    receipts[key] = template.copy()
                fulfill(route, receipts[key])
        elif path.startswith("/template-requests/"):
            key = path.rsplit("/", 1)[1]
            if state["lookup_missing"] or key not in receipts:
                fulfill(route, {"detail": "not found"}, 404)
            else:
                fulfill(route, receipts[key])
        else:
            raise AssertionError(f"Unexpected API call: {request.method} {path}")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context()
            page = context.new_page()
            other = context.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            other.on("pageerror", lambda error: errors.append(str(error)))
            page.on("dialog", lambda dialog: dialog.accept())
            context.route("**/api/business/**", api)
            page.goto(base_url, wait_until="networkidle")
            other.goto(base_url, wait_until="networkidle")
            page.locator("#template-manager > summary").click()
            other.locator("#template-manager > summary").click()
            page.get_by_role("button", name="初版 · v1").click()
            other.get_by_role("button", name="初版 · v1").click()
            page.locator('.template-editor [name="template-name"]').fill("新版")
            page.get_by_role("button", name="保存新版本").click()
            page.get_by_text("模板写入结果未确认", exact=False).wait_for()
            assert template["version"] == 2 and len(writes) == 1
            first_key = writes[0][0]
            assert len(first_key) >= 16
            assert page.get_by_role("button", name="保存新版本").is_disabled()
            other.get_by_text(first_key, exact=False).wait_for()
            assert other.get_by_role("button", name="保存新版本").is_disabled()

            page.reload(wait_until="networkidle")
            page.locator("#template-manager > summary").click()
            assert page.get_by_text(first_key, exact=False).count() == 1
            page.get_by_role("button", name="核对模板写入结果").click()
            page.get_by_text("404 不代表写入未受理", exact=False).wait_for()
            assert len(writes) == 1
            state["lookup_missing"] = False
            page.get_by_role("button", name="核对模板写入结果").click()
            page.get_by_text("新版 第 2 版已保存。").wait_for()
            assert len(writes) == 1 and template["version"] == 2
            other.get_by_text("请刷新页面核对后再写入", exact=False).wait_for()
            assert other.get_by_role("button", name="保存新版本").is_disabled()
            other.reload(wait_until="networkidle")
            other.locator("#template-manager > summary").click()
            other.get_by_role("button", name="新版 · v2").wait_for()

            page.get_by_role("button", name="新版 · v2").click()
            page.locator('.template-editor [name="template-name"]').fill("三版")
            state["mode"] = "not_accepted_error"
            page.get_by_role("button", name="保存新版本").click()
            page.get_by_text("模板写入结果未确认", exact=False).wait_for()
            second_key = writes[-1][0]
            assert second_key != first_key and template["version"] == 2
            page.get_by_role("button", name="核对模板写入结果").click()
            page.get_by_text("404 不代表写入未受理", exact=False).wait_for()
            state["mode"] = "success"
            page.get_by_role("button", name="确认后同键重试").click()
            page.get_by_text("三版 第 3 版已保存。").wait_for()
            assert writes[-1][0] == second_key and writes[-1][1] == writes[-2][1]
            assert template["version"] == 3
            page.evaluate("""() => {
                const original = Storage.prototype.setItem;
                Storage.prototype.setItem = function(key, value) {
                    if (key.startsWith('mineru.business.pendingTemplateWrite.v2.')) throw new Error('storage disabled');
                    return original.call(this, key, value);
                };
            }""")
            page.locator('.template-editor [name="template-name"]').fill("四版草稿")
            page.get_by_role("button", name="保存新版本").click()
            page.get_by_text("无法保存模板请求键", exact=False).wait_for()
            assert page.locator('.template-editor [name="template-name"]').input_value() == "四版草稿"
            assert len(writes) == 3 and not errors, errors
            print("Playwright template unknown outcomes passed: reload, same-key replay, cross-tab write guard")
        finally:
            browser.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18088")
