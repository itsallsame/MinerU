"""Exercise the built Web against real local business API and Doclib, without route mocks."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


def main(base_url: str, sample: Path, office_files: list[Path], result_receipt: Path, structure_receipt: Path) -> None:
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
            documents = page.request.get(f"{base_url}/api/business/documents?limit=20&offset=0").json()["items"]
            pdf_document = next(item["document"] for item in documents if item["document"]["original_name"] == sample.name)
            revisions = page.request.get(f"{base_url}/api/business/documents/{pdf_document['id']}/revisions").json()
            pdf_revision = revisions[0]
            structure_url = f"{base_url}/api/business/revisions/{pdf_revision['id']}/structure/derived?page_no=1"
            native_structure = page.request.get(structure_url).json()
            assert native_structure["version"] == 0 and native_structure["state"] == "historical_parse_unconfirmed"
            native_url = f"{base_url}/api/business/revisions/{pdf_revision['id']}/structure?page_no=1"
            original_native_structure = page.request.get(native_url).json()

            def walk(nodes: list[dict[str, object]]) -> list[dict[str, object]]:
                return [node for item in nodes for node in [item, *walk(item["children"])]]

            original_nodes = walk(native_structure["nodes"])
            editable_node = next((node for node in original_nodes if node["origins"] and node["type"] != "text"), None)
            assert editable_node is not None, "Public PDF must expose at least one non-text native structure node"
            structure = page.locator(".review-section").filter(has=page.get_by_role("heading", name="文档块结构复核"))
            structure.get_by_role("button", name="查看本页块").click()
            structure.get_by_text("版本 v0").wait_for(timeout=30000)
            row = structure.locator(".structure-block").filter(has_text=editable_node["node_id"])
            row.get_by_role("button", name="选择编辑此节点").click()
            structure.get_by_label("新类型").fill("text")
            structure.get_by_label("段落标题级别（其他类型留空）").fill("")
            structure.get_by_label("编辑原因").fill("本地链路冒烟，不评价解析质量")
            structure.get_by_role("button", name="保存结构编辑").click()
            structure.get_by_text("版本 v1").wait_for(timeout=30000)
            derived_structure = page.request.get(structure_url).json()
            assert derived_structure["version"] == 1
            assert derived_structure["source"] == "web"
            assert derived_structure["operation"]["node_id"] == editable_node["node_id"]
            changed_node = next(
                node for node in walk(derived_structure["nodes"])
                if node["node_id"] == editable_node["node_id"]
            )
            assert changed_node["type"] == "text"
            assert page.request.get(native_url).json() == original_native_structure
            structure_receipt.write_text(json.dumps({
                "revision_id": pdf_revision["id"], "page_no": 1, "version": 1,
                "node_id": editable_node["node_id"],
            }), encoding="utf-8")

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
                    manual_start = page.get_by_role("button", name="生成字段候选", exact=True)
                    if manual_start.count():
                        manual_start.click()  # If the run has not appeared yet, use the same idempotent enqueue route.
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
                    result_receipt.write_text(json.dumps(exported, ensure_ascii=False), encoding="utf-8")
                    page.reload(wait_until="networkidle")
                    page.get_by_role("button", name=f"查看 {office.name}，已解析").click()
                    page.get_by_text("确认成果 v1", exact=True).wait_for(timeout=30000)
            assert not errors, errors
            print("Live Web smoke passed: native formats, structure edit, historical read, review and JSON export")
        finally:
            browser.close()


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]), [Path(path) for path in sys.argv[3:-2]], Path(sys.argv[-2]), Path(sys.argv[-1]))
