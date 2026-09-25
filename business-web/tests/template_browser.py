"""Playwright template CRUD against the real offline Web bundle and simulated API."""

from __future__ import annotations

import json
import sys
import time

from playwright.sync_api import sync_playwright


def main(base_url: str) -> None:
    now = int(time.time() * 1000)
    builtin = {
        "code": "official_document", "name": "公文", "version": 1, "built_in": True,
        "enabled": True, "fields": [{"code": "title", "label": "标题", "type": "text", "required": True}],
        "created_at_ms": now,
    }
    templates = [builtin]
    writes: list[tuple[str, dict[str, object]]] = []
    refresh_failures = {"enabled": False}

    def fulfill(route: object, payload: object, status: int = 200) -> None:
        route.fulfill(status=status, content_type="application/json", body=json.dumps(payload, ensure_ascii=False))

    def api(route: object) -> None:
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
            if refresh_failures["enabled"]:
                fulfill(route, {"detail": "templates_unavailable"}, 503)
            else:
                fulfill(route, templates)
        elif path == "/templates" and request.method == "POST":
            payload = json.loads(request.post_data)
            writes.append(("create", payload))
            template = {**payload, "version": 1, "built_in": False, "enabled": True, "created_at_ms": now}
            templates.append(template)
            fulfill(route, template, 201)
        elif path == "/templates/my_report" and request.method == "PUT":
            payload = json.loads(request.post_data)
            writes.append(("update", payload))
            templates[1] = {**templates[1], **payload, "version": 2}
            fulfill(route, templates[1])
        elif path == "/templates/my_report/disable" and request.method == "POST":
            writes.append(("disable", {}))
            templates[1] = {**templates[1], "enabled": False}
            fulfill(route, templates[1])
        else:
            raise AssertionError(f"Unexpected API call: {request.method} {path}")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("dialog", lambda dialog: dialog.accept())
            page.route("**/api/business/**", api)
            page.goto(base_url, wait_until="networkidle")
            page.locator("#template-manager > summary").click()
            page.get_by_role("button", name="公文 · v1 · 内置").focus()
            page.keyboard.press("Enter")
            page.get_by_text("内置模板只读；需要不同字段时").wait_for()
            assert page.get_by_role("button", name="公文 · v1 · 内置").evaluate(
                "node => document.activeElement === node && getComputedStyle(node).outlineStyle !== 'none'"
            )
            assert page.locator(".template-editor").count() == 0

            page.get_by_role("button", name="新增自定义模板").focus()
            page.keyboard.press("Enter")
            editor = page.locator(".template-editor")
            assert editor.locator('[name="template-code"]').evaluate(
                "node => document.activeElement === node && getComputedStyle(node).outlineStyle !== 'none'"
            )
            editor.get_by_role("button", name="移除").focus()
            page.keyboard.press("Enter")
            assert editor.get_by_role("button", name="添加字段").evaluate(
                "node => document.activeElement === node && getComputedStyle(node).outlineStyle !== 'none'"
            )
            page.keyboard.press("Enter")
            assert editor.locator('[name="field-code"]').evaluate(
                "node => document.activeElement === node && getComputedStyle(node).outlineStyle !== 'none'"
            )
            editor.locator('[name="template-code"]').fill("my_report")
            editor.locator('[name="template-name"]').fill("我的报告")
            editor.locator('[name="field-code"]').fill("title")
            editor.locator('[name="field-label"]').fill("标题")
            editor.locator('[name="field-required"]').check()
            editor.get_by_role("button", name="添加字段").click()
            second = editor.locator(".template-field-row").nth(1)
            assert second.locator('[name="field-code"]').evaluate(
                "node => document.activeElement === node && getComputedStyle(node).outlineStyle !== 'none'"
            )
            second.locator('[name="field-code"]').fill("written_date")
            second.locator('[name="field-label"]').fill("日期")
            second.locator('[name="field-type"]').select_option("date")
            second.locator('[name="field-code"]').fill("title")
            editor.get_by_role("button", name="创建模板").click()
            page.get_by_role("alert").get_by_text("字段代码须唯一", exact=False).wait_for()
            assert editor.locator('[name="template-code"]').input_value() == "my_report"
            assert editor.locator(".template-field-row").nth(1).locator('[name="field-code"]').input_value() == "title"
            assert not writes
            second.locator('[name="field-code"]').fill("written_date")
            editor.get_by_role("button", name="添加字段").click()
            editor.get_by_role("button", name="添加字段").click()
            third = editor.locator(".template-field-row").nth(2)
            third.get_by_role("button", name="移除").focus()
            page.keyboard.press("Enter")
            assert editor.locator(".template-field-row").nth(2).locator('[name="field-code"]').evaluate(
                "node => document.activeElement === node"
            )
            editor.locator(".template-field-row").nth(2).get_by_role("button", name="移除").focus()
            page.keyboard.press("Enter")
            assert second.locator('[name="field-code"]').evaluate("node => document.activeElement === node")
            second.get_by_role("button", name="上移").click()
            refresh_failures["enabled"] = True
            editor.get_by_role("button", name="创建模板").click()
            page.get_by_text("我的报告 第 1 版已保存。模板列表刷新失败", exact=False).wait_for()
            assert page.get_by_role("alert").count() == 0
            assert [field["code"] for field in writes[0][1]["fields"]] == ["written_date", "title"]
            assert writes[0][1]["fields"][1]["required"] is True
            assert page.locator("#upload-template option[value='my_report']").count() == 1

            refresh_failures["enabled"] = False
            page.locator('.template-editor [name="template-name"]').fill("我的报告新版")
            page.get_by_role("button", name="保存新版本").click()
            page.get_by_text("我的报告新版 第 2 版已保存。").wait_for()
            assert writes[1][0] == "update" and writes[1][1]["name"] == "我的报告新版"
            refresh_failures["enabled"] = True
            page.get_by_role("button", name="停用此自定义模板").click()
            page.get_by_text("我的报告新版 已停用；历史版本仍可读取。模板列表刷新失败", exact=False).wait_for()
            assert writes[2][0] == "disable"
            assert page.locator("#upload-template option[value='my_report']").is_disabled()
            assert page.locator(".template-editor").count() == 0
            assert page.get_by_role("alert").count() == 0
            assert not errors, errors
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            print("Playwright template workflow passed: built-in read-only, custom create/version/disable, field order")
        finally:
            browser.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18088")
