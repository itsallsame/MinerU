"""Playwright browser smoke against a running business Web/API of this checkout.

Run with an installed Playwright Chromium: python browser_smoke.py http://127.0.0.1:18088
The page and capability requests are real; upload/list responses are intercepted
to exercise UI transitions without requiring a model-backed Doclib worker.
"""

from __future__ import annotations

import json
import sys
import time

from playwright.sync_api import sync_playwright


def main(base_url: str) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base_url, wait_until="networkidle")
            page.get_by_text("业务服务已连接").wait_for()
            assert page.get_by_text("0 份文档").count() == 1
            assert page.get_by_role("button", name="开始上传与解析").is_disabled()

            sent_bodies: list[bytes] = []
            list_urls: list[str] = []
            document = {
                "id": "a" * 32,
                "original_name": "report.pdf",
                "sha256": "b" * 64,
                "size": 12,
                "created_at_ms": int(time.time() * 1000),
                "template_code": None,
                "template_version": None,
            }
            task = {
                "id": "c" * 32,
                "document_id": document["id"],
                "requested_tier": "standard",
                "actual_tier": None,
                "status": "submitted",
                "error_code": None,
                "created_at_ms": document["created_at_ms"],
                "updated_at_ms": document["created_at_ms"],
            }

            def upload(route: object) -> None:
                sent_bodies.append(route.request.post_data_buffer)
                route.fulfill(
                    status=202, content_type="application/json", body=json.dumps({"document": document, "task": task}),
                )

            def list_documents(route: object) -> None:
                list_urls.append(route.request.url)
                route.fulfill(
                    status=200, content_type="application/json",
                    body=json.dumps({"items": [{"document": document, "task": task}], "total": 1, "limit": 20, "offset": 0}),
                )

            page.route("**/api/business/documents", upload)
            page.route("**/api/business/documents?*", list_documents)
            page.route("**/api/business/documents/*/revisions", lambda route: route.fulfill(
                status=200, content_type="application/json", body="[]",
            ))
            page.route("**/api/business/documents/*/source", lambda route: route.fulfill(
                status=200, content_type="application/pdf", body=b"%PDF-1.4\n",
            ))

            page.locator("#files").set_input_files({
                "name": "bad.exe", "mimeType": "application/octet-stream", "buffer": b"bad",
            })
            page.get_by_role("button", name="开始上传与解析").click()
            page.get_by_text("不支持 .exe 文件").wait_for()
            assert not sent_bodies

            page.locator("#files").set_input_files({
                "name": "report.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4\nabc",
            })
            page.get_by_role("button", name="开始上传与解析").click()
            page.get_by_text("已受理 · 解析中").wait_for()
            assert len(sent_bodies) == 1
            assert b'name="tier"' in sent_bodies[0] and b"standard" in sent_bodies[0]
            page.locator("#files").set_input_files({"name": "memo.html", "mimeType": "text/html", "buffer": b"<h1>memo</h1>"})
            page.get_by_role("button", name="开始上传与解析").click()
            page.get_by_text("memo.html · 已受理").wait_for()
            assert len(sent_bodies) == 2
            assert b'name="tier"' not in sent_bodies[1]
            page.locator("#files").set_input_files([
                {"name": "rejected.exe", "mimeType": "application/octet-stream", "buffer": b"bad"},
                {"name": "image.png", "mimeType": "image/png", "buffer": b"png"},
            ])
            page.get_by_role("button", name="开始上传与解析").click()
            page.get_by_text("rejected.exe · 不支持").wait_for()
            page.get_by_text("image.png · 已受理").wait_for()
            assert len(sent_bodies) == 3
            with page.expect_response(
                lambda response: "/api/business/documents?" in response.url and "status=failed" in response.url
            ):
                page.locator("#status-filter").select_option("failed")
            assert "status=failed" in list_urls[-1]
            with page.expect_response(lambda response: "template_code=official_document" in response.url):
                page.locator("#template-filter").select_option("official_document")
            assert "template_code=official_document" in list_urls[-1]
            page.get_by_role("button", name="查看 report.pdf，解析中").click()
            page.get_by_title("report.pdf PDF 原文预览").wait_for()

            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            assert not errors, errors
            print("Playwright business Web smoke passed: open API, validation, tier/Flash, filters, detail, mobile width")
        finally:
            browser.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18088")
