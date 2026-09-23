"""PDF/image preview and Office download-only behavior in one browser session."""

from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/lXcAAAAASUVORK5CYII="
)
PDF_SAMPLE = (Path(__file__).resolve().parents[2] / "demo" / "pdfs" / "demo1.pdf").read_bytes()


def main(base_url: str) -> None:
    now = int(time.time() * 1000)
    documents = [
        {"id": char * 32, "original_name": name, "sha256": char * 64,
         "size": 16, "created_at_ms": now, "template_code": None, "template_version": None}
        for char, name in (("a", "paper.pdf"), ("b", "scan.png"), ("c", "report.docx"))
    ]
    head_calls: list[str] = []
    get_calls: list[str] = []

    def api(route: object) -> None:
        request = route.request
        path = request.url.split("/api/business", 1)[1].split("?", 1)[0]
        if path == "/capabilities":
            payload = {"max_upload_bytes": 1000000, "parseable_extensions": ["pdf", "png", "docx"],
                       "tiered_extensions": ["pdf", "png"], "flash_only_extensions": ["docx"],
                       "tiers": ["flash", "basic", "standard", "advanced"]}
        elif path == "/templates":
            payload = []
        elif path == "/documents":
            payload = {"items": [{"document": doc, "task": None} for doc in documents],
                       "total": 3, "limit": 20, "offset": 0}
        elif path in (f"/documents/{doc['id']}/revisions" for doc in documents):
            payload = []
        elif path.endswith("/source"):
            document_id = path.split("/")[2]
            if request.method == "HEAD":
                head_calls.append(document_id)
                route.fulfill(status=200, body="")
                return
            get_calls.append(document_id)
            content_type, body = (
                ("application/pdf", PDF_SAMPLE) if document_id == documents[0]["id"]
                else ("image/png", PNG_1PX) if document_id == documents[1]["id"]
                else ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", b"docx")
            )
            headers = (
                {"Content-Disposition": 'attachment; filename="report.docx"'}
                if document_id == documents[2]["id"] else None
            )
            route.fulfill(status=200, content_type=content_type, headers=headers, body=body)
            return
        else:
            raise AssertionError(f"Unexpected API call: {request.method} {path}")
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.context.route("**/api/business/**", api)
            page.goto(base_url, wait_until="networkidle")

            page.get_by_role("button", name="查看 paper.pdf，无任务").click()
            preview = page.get_by_title("paper.pdf PDF 原文预览")
            preview.wait_for()
            assert preview.get_attribute("src").endswith("/source#page=1")
            page.get_by_label("查看 PDF 原文页码").fill("2")
            page.get_by_role("button", name="打开页并查关联字段").click()
            assert preview.get_attribute("src").endswith("/source#page=2")
            assert page.get_by_role("link", name="在新窗口打开原文").count() == 1

            page.get_by_role("button", name="查看 scan.png，无任务").click()
            image = page.get_by_alt_text("scan.png 原图")
            image.wait_for()
            page.wait_for_function("document.querySelector('img.source-preview')?.naturalWidth === 1")
            assert page.locator("iframe.source-preview").count() == 0
            assert page.get_by_role("button", name="查找此图的关联字段").count() == 1

            page.get_by_role("button", name="查看 report.docx，无任务").click()
            page.get_by_text("该格式暂不支持浏览器原文预览").wait_for()
            assert page.locator("iframe.source-preview, img.source-preview").count() == 0
            assert page.get_by_label("查看 PDF 原文页码").count() == 0
            download = page.get_by_role("link", name="下载原文件")
            assert download.get_attribute("href").endswith(f"/documents/{documents[2]['id']}/source")
            assert download.get_attribute("download") == "report.docx"
            assert download.get_attribute("target") is None
            assert head_calls == [doc["id"] for doc in documents]
            assert documents[0]["id"] in get_calls and documents[1]["id"] in get_calls
            assert not errors, errors
            print("Playwright source formats passed: PDF page, decoded image, Office download-only link")
        finally:
            browser.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18088")
