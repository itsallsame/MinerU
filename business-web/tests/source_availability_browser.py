"""Missing or changed originals must not be presented as working previews."""

from __future__ import annotations

import json
import sys
import time

from playwright.sync_api import sync_playwright


def main(base_url: str) -> None:
    now = int(time.time() * 1000)
    documents = [
        {"id": char * 32, "original_name": name, "sha256": char * 64, "size": 16,
         "created_at_ms": now, "template_code": None, "template_version": None}
        for char, name in [("a", "report.pdf"), ("b", "scan.png"), ("c", "letter.html")]
    ]
    source_status = {documents[0]["id"]: 409, documents[1]["id"]: 404, documents[2]["id"]: 503}
    head_calls: list[str] = []

    def respond(route: object, payload: object, status: int = 200) -> None:
        route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))

    def api(route: object) -> None:
        path = route.request.url.split("/api/business", 1)[1].split("?", 1)[0]
        if path == "/capabilities":
            respond(route, {"max_upload_bytes": 1000000, "parseable_extensions": ["pdf", "png", "html"],
                            "tiered_extensions": ["pdf", "png"], "flash_only_extensions": ["html"],
                            "tiers": ["flash", "basic", "standard", "advanced"]})
        elif path == "/templates":
            respond(route, [])
        elif path == "/documents":
            respond(route, {"items": [{"document": doc, "task": None} for doc in documents],
                            "total": 3, "limit": 20, "offset": 0})
        elif path.endswith("/revisions"):
            respond(route, [])
        elif path.endswith("/source"):
            document_id = path.split("/")[2]
            if route.request.method == "HEAD":
                head_calls.append(document_id)
                route.fulfill(status=source_status[document_id], body="")
            else:
                route.fulfill(
                    status=200, content_type="image/png" if document_id == documents[1]["id"] else "application/pdf",
                    body=b"not-a-decodable-image" if document_id == documents[1]["id"] else b"%PDF-1.4\n",
                )
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

            page.get_by_role("button", name="查看 report.pdf，无任务").click()
            page.get_by_text("原件缺失或内容与上传记录不一致").wait_for()
            assert page.locator("#detail-content iframe.source-preview").count() == 0
            assert page.locator("#detail-content a[href$='/source']").count() == 0
            source_status[documents[0]["id"]] = 200
            retry_source = page.get_by_role("button", name="重试核验原件")
            retry_source.focus()
            page.keyboard.press("Enter")
            page.locator("#detail-content iframe.source-preview").wait_for()
            assert page.locator("#detail-content a[href$='/source']").count() == 1
            assert page.get_by_role("link", name="在新窗口打开原文").evaluate(
                "node => document.activeElement === node"
            ), "keyboard source retry lost focus after the detail panel was replaced"
            page.evaluate("""() => {
                document.querySelector('[data-source-primary]').dataset.oldSourceLink = 'true';
                document.getElementById('refresh').click();
            }""")
            page.wait_for_function("!document.querySelector('[data-source-primary]')?.dataset.oldSourceLink")
            assert page.get_by_role("link", name="在新窗口打开原文").evaluate(
                "node => document.activeElement === node"
            ), "background document refresh lost focus on the source action"

            page.get_by_role("button", name="查看 scan.png，无任务").click()
            page.get_by_text("业务文档已不存在，原件无法读取").wait_for()
            assert page.locator("#detail-content img.source-preview").count() == 0
            assert page.locator("#detail-content a[href$='/source']").count() == 0
            source_status[documents[1]["id"]] = 200
            page.get_by_role("button", name="重试核验原件").focus()
            page.keyboard.press("Enter")
            page.get_by_text("浏览器无法加载原图").wait_for()
            assert page.locator("#detail-content img.source-preview").count() == 0
            assert page.locator("#detail-content a[href$='/source']").count() == 0
            assert page.get_by_role("button", name="重试核验原件").evaluate(
                "node => document.activeElement === node"
            ), "image decode failure did not return focus to the retry action"

            page.get_by_role("button", name="查看 letter.html，无任务").click()
            page.get_by_text("原件暂不可用（503）").wait_for()
            assert page.locator("#detail-content a[href$='/source']").count() == 0
            assert head_calls == [documents[0]["id"]] * 2 + [documents[1]["id"]] * 2 + [documents[2]["id"]]
            assert not errors, errors
            print("Playwright original availability passed: 409/404/503, no false preview, explicit retry")
        finally:
            browser.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18088")
