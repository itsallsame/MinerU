"""Exercise the built Web against real local business API and Doclib, without route mocks."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


def main(base_url: str, sample: Path, office_files: list[Path]) -> None:
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

            page.locator("#upload-template").select_option("paper")
            page.locator("#files").set_input_files([str(path) for path in office_files])
            page.get_by_role("button", name="开始上传与解析").click()
            for office in office_files:
                page.get_by_text(f"{office.name} · 已受理").wait_for(timeout=30000)
            for office in office_files:
                card = page.get_by_role("button", name=f"查看 {office.name}，已解析")
                card.wait_for(timeout=90000)
                card.click()
                page.locator(".revision-item").filter(has_text="FLASH · 第 1 页").wait_for(timeout=30000)
                page.get_by_role("button", name="读取这一页").click()
                parsed = page.locator(".historical-content")
                parsed.wait_for(timeout=30000)
                marker = f"MinerUOffice{office.suffix[1:].capitalize()}Marker"
                assert marker in parsed.inner_text(), f"Missing native content from {office.name}"
                assert page.get_by_text("该格式暂不支持浏览器原文预览", exact=False).count() == 1
                if office.suffix == ".docx":
                    page.get_by_role("button", name="生成字段候选").click()
                    title_field = page.locator('.field-review[data-field-code="title"]')
                    candidate = title_field.locator(".candidate-row").filter(has_text=marker)
                    candidate.wait_for(timeout=30000)
                    confirm = page.get_by_role("button", name="确认并生成不可变成果版本")
                    assert confirm.is_disabled(), "A machine candidate must not be confirmed without review"
                    candidate.get_by_role("button", name="接受候选").click()
                    title_field.get_by_text(f"已复核：{marker}", exact=False).wait_for(timeout=30000)
                    confirm.wait_for(state="visible")
                    assert confirm.is_enabled()
                    confirm.click()
                    page.get_by_text("确认成果 v1", exact=True).wait_for(timeout=30000)
                    assert marker in page.locator(".result-card").inner_text()
                    with page.expect_download() as receipt:
                        page.get_by_role("button", name="下载 JSON").click()
                    exported = json.loads(Path(receipt.value.path()).read_text(encoding="utf-8"))
                    assert any(field["value"] == marker for field in exported["fields"])
                    page.reload(wait_until="networkidle")
                    page.get_by_role("button", name=f"查看 {office.name}，已解析").click()
                    page.get_by_text("确认成果 v1", exact=True).wait_for(timeout=30000)
            assert not errors, errors
            print("Live Web smoke passed: native formats, historical read, review, confirmation and JSON export")
        finally:
            browser.close()


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]), [Path(path) for path in sys.argv[3:]])
