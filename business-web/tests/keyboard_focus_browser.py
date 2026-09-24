"""Playwright keyboard focus regression against the offline Web bundle.

Run with a local static server, for example:
python3 -m http.server 18088 --bind 127.0.0.1 --directory business-web/dist
python3 business-web/tests/keyboard_focus_browser.py http://127.0.0.1:18088
"""

from __future__ import annotations

import json
import sys
import time

from playwright.sync_api import sync_playwright


def main(base_url: str) -> None:
    now = int(time.time() * 1000)
    record = {
        "id": "a" * 32,
        "original_name": "report.pdf",
        "sha256": "b" * 64,
        "size": 12,
        "created_at_ms": now,
        "template_code": None,
        "template_version": None,
    }
    task = {
        "id": "c" * 32,
        "document_id": record["id"],
        "requested_tier": "flash",
        "actual_tier": "flash",
        "status": "done",
        "error_code": None,
        "created_at_ms": now,
        "updated_at_ms": now,
    }

    def route_api(route: object) -> None:
        path = route.request.url.split("/api/business", 1)[1].split("?", 1)[0]
        if path == "/capabilities":
            payload = {
                "max_upload_bytes": 1000000,
                "parseable_extensions": ["pdf"],
                "tiered_extensions": ["pdf"],
                "flash_only_extensions": [],
                "tiers": ["flash", "basic", "standard", "advanced"],
            }
        elif path == "/templates":
            payload = []
        elif path == "/documents":
            payload = {"items": [{"document": record, "task": task}], "total": 1, "limit": 20, "offset": 0}
        elif path == f"/documents/{record['id']}/revisions":
            payload = []
        elif path == f"/documents/{record['id']}/source":
            route.fulfill(status=200, content_type="application/pdf", body=b"%PDF-1.4\n")
            return
        else:
            raise AssertionError(f"Unexpected business API path: {path}")
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/business/**", route_api)
            page.goto(base_url, wait_until="networkidle")
            page.keyboard.press("Tab")
            assert page.evaluate("document.activeElement?.classList.contains('skip-link')")
            page.keyboard.press("Enter")
            assert page.evaluate("location.hash === '#main'")
            assert page.evaluate("document.activeElement?.id === 'main'"), "skip link did not move focus to main"
            assert page.evaluate("getComputedStyle(document.activeElement).outlineStyle !== 'none'"), (
                "skip-link target has no visible focus indicator"
            )
            page.keyboard.press("Tab")
            assert page.evaluate("document.activeElement?.id === 'refresh'"), "Tab did not enter main content"
            card = page.get_by_role("button", name="查看 report.pdf，已解析")
            card.wait_for()
            card.focus()
            page.keyboard.press("Enter")
            page.get_by_role("heading", name="report.pdf").wait_for()
            assert card.get_attribute("aria-current") == "true"
            assert card.evaluate("node => document.activeElement === node"), "document selection lost keyboard focus"
            assert card.evaluate("node => getComputedStyle(node).outlineStyle !== 'none'")
            assert not errors, errors
            print("Playwright keyboard focus passed: skip link enters main and selected document keeps focus")
        finally:
            browser.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18088")
