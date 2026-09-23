"""Exercise the built Web against a real local business API and Doclib, without route mocks."""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


def main(base_url: str, sample: Path) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base_url, wait_until="networkidle")
            page.get_by_text("业务服务已连接").wait_for()
            page.get_by_text("0 份文档").wait_for()

            page.locator("#upload-tier").select_option("flash")
            page.locator("#files").set_input_files(str(sample))
            page.get_by_role("button", name="开始上传与解析").click()
            page.get_by_text("demo1.pdf · 已受理").wait_for(timeout=30000)
            page.get_by_role("button", name="查看 demo1.pdf，已解析").wait_for(timeout=90000)
            page.get_by_role("button", name="查看 demo1.pdf，已解析").click()
            page.locator(".revision-item").filter(has_text="FLASH · 第 1-13 页").wait_for(timeout=30000)
            page.get_by_role("button", name="读取这一页").click()
            content = page.locator(".historical-content")
            content.wait_for(timeout=30000)
            assert "afforestation" in content.inner_text().lower()
            assert page.get_by_text("机器解析文本，未人工确认", exact=False).count() >= 1
            assert not errors, errors
            print("Live Web smoke passed: real PDF upload, Flash parse, revision and historical page read")
        finally:
            browser.close()


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
