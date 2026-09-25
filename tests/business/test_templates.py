"""Open template definitions are versioned contracts, not permission records."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from mineru.business.api import create_app
from mineru.business.documents import ImmutableUploadStore
from mineru.business.domain import TemplateField
from mineru.business.store import BusinessStore, BusinessStoreError, TemplateRequestConflict


def _store(tmp_path: Path) -> BusinessStore:
    store = BusinessStore(tmp_path / "business.sqlite3")
    store.initialize()
    return store


def test_builtin_templates_are_seeded_once_with_expected_required_fields(tmp_path: Path) -> None:
    store = _store(tmp_path)
    templates = {template.code: template for template in store.list_templates()}
    assert set(templates) == {"official_document", "paper", "research_report", "newspaper"}
    assert all(template.built_in and template.enabled and template.version == 1 for template in templates.values())
    assert {field.code for field in templates["official_document"].fields if field.required} == {"title"}
    assert {field.code for field in templates["newspaper"].fields if field.required} == {
        "newspaper_name", "article_title"
    }
    assert store.get_template("paper").fields[-1].type == "list"
    reopened = BusinessStore(tmp_path / "business.sqlite3")
    reopened.initialize()
    assert reopened.list_templates() == store.list_templates()


def test_custom_template_versions_are_immutable_and_disable_preserves_history(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.create_template(code="my_contract", name="合同", fields=(TemplateField("title", "标题", required=True),))
    second = store.update_template(
        "my_contract", name="合同二版", fields=(TemplateField("title", "标题"), TemplateField("date", "日期", "date"))
    )
    assert first.version == 1 and second.version == 2
    assert store.get_template("my_contract", version=1) == first
    assert store.get_template("my_contract").fields[1].type == "date"
    assert store.disable_template("my_contract").enabled is False
    assert store.get_template("my_contract", version=1).fields == first.fields
    with pytest.raises(BusinessStoreError, match="Disabled"):
        store.update_template("my_contract", name="三版", fields=first.fields)


def test_template_validation_and_builtin_protection(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="unique"):
        store.create_template(code="duplicate_fields", name="重复", fields=(TemplateField("a", "甲"), TemplateField("a", "乙")))
    with pytest.raises(ValueError, match="lowercase"):
        store.create_template(code="Bad-Code", name="错误", fields=(TemplateField("a", "甲"),))
    with pytest.raises(BusinessStoreError, match="already exists"):
        store.create_template(code="paper", name="假论文", fields=(TemplateField("a", "甲"),))
    with pytest.raises(BusinessStoreError, match="Built-in"):
        store.update_template("paper", name="修改", fields=(TemplateField("a", "甲"),))
    with pytest.raises(BusinessStoreError, match="Only custom"):
        store.disable_template("paper")


def test_template_write_keys_replay_exact_version_without_duplicate_update(tmp_path: Path) -> None:
    store = _store(tmp_path)
    fields = (TemplateField("title", "标题"),)
    create_key, update_key, disable_key = (character * 20 for character in "cud")
    first = store.create_template(code="my_report", name="报告", fields=fields, request_key=create_key)
    assert store.create_template(code="my_report", name="报告", fields=fields, request_key=create_key) == first
    second = store.update_template("my_report", name="新版", fields=fields, request_key=update_key)
    assert second.version == 2
    assert store.update_template("my_report", name="新版", fields=fields, request_key=update_key) == second
    store.update_template("my_report", name="三版", fields=fields)
    assert store.get_template_request(update_key) == second
    assert store.update_template(
        "my_report", name="新版", fields=fields, request_key=update_key, expected_version=1,
    ) == second
    with pytest.raises(TemplateRequestConflict):
        store.update_template("my_report", name="不同内容", fields=fields, request_key=update_key)
    with pytest.raises(TemplateRequestConflict):
        store.disable_template("my_report", request_key=update_key)
    disabled = store.disable_template("my_report", request_key=disable_key)
    assert disabled.version == 3 and not disabled.enabled
    assert store.disable_template("my_report", request_key=disable_key) == disabled
    with pytest.raises(TemplateRequestConflict, match="version precondition"):
        store.disable_template("my_report", request_key=disable_key, expected_version=2)
    assert store.get_template_request(create_key) == first
    assert store.get_template_request("missing_key_123456") is None
    with pytest.raises(BusinessStoreError, match="Invalid template idempotency key"):
        store.get_template_request("short")


def test_template_stale_expected_version_cannot_overwrite_or_disable(tmp_path: Path) -> None:
    store = _store(tmp_path)
    fields = (TemplateField("title", "标题"),)
    store.create_template(code="my_report", name="初版", fields=fields)
    first = store.update_template(
        "my_report", name="二版", fields=fields, expected_version=1, request_key="first_update_123456",
    )
    assert first.version == 2
    with pytest.raises(BusinessStoreError, match="Template version changed"):
        store.update_template(
            "my_report", name="过期覆盖", fields=fields, expected_version=1, request_key="stale_update_123456",
        )
    with pytest.raises(BusinessStoreError, match="Template version changed"):
        store.disable_template("my_report", expected_version=1, request_key="stale_disable_12345")
    assert store.get_template("my_report").name == "二版"
    assert store.get_template("my_report").enabled is True
    assert store.get_template_request("stale_update_123456") is None
    assert store.get_template_request("stale_disable_12345") is None
    assert store.update_template(
        "my_report", name="二版", fields=fields, expected_version=1, request_key="first_update_123456",
    ) == first
    with pytest.raises(TemplateRequestConflict, match="version precondition"):
        store.update_template(
            "my_report", name="二版", fields=fields, expected_version=2, request_key="first_update_123456",
        )


def test_template_api_is_open_and_old_version_is_readable(tmp_path: Path) -> None:
    store = _store(tmp_path)
    client = TestClient(create_app(workflow=Mock(), store=store, evidence_reader=Mock(), evidence_writer=Mock()))
    assert len(client.get("/api/business/templates").json()) == 4
    created = client.post("/api/business/templates", json={
        "code": "sample", "name": "样本", "fields": [{"code": "title", "label": "标题", "required": True}]
    })
    assert created.status_code == 201
    assert created.json()["version"] == 1
    assert "owner_id" not in created.json()
    updated = client.put("/api/business/templates/sample", headers={"If-Match": '"1"'}, json={
        "name": "样本二版", "fields": [{"code": "date", "label": "日期", "type": "date"}]
    })
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert client.get("/api/business/templates/sample?version=1").json()["fields"][0]["code"] == "title"
    assert client.get("/api/business/templates/sample?version=3").status_code == 404
    assert client.post("/api/business/templates/sample/disable", headers={"If-Match": '"2"'}).json()["enabled"] is False


def test_template_api_request_lookup_and_replay(tmp_path: Path) -> None:
    store = _store(tmp_path)
    client = TestClient(create_app(workflow=Mock(), store=store, evidence_reader=Mock(), evidence_writer=Mock()))
    key = "template_update_123456"
    body = {"name": "二版", "fields": [{"code": "title", "label": "标题"}]}
    store.create_template(code="sample", name="一版", fields=(TemplateField("title", "标题"),))
    headers = {"Idempotency-Key": key, "If-Match": '"1"'}
    first = client.put("/api/business/templates/sample", json=body, headers=headers)
    assert first.status_code == 200 and first.json()["version"] == 2
    assert client.put("/api/business/templates/sample", json=body, headers=headers).json() == first.json()
    assert client.get(f"/api/business/template-requests/{key}").json() == first.json()
    assert store.get_template("sample").version == 2
    changed = {"name": "三版", "fields": body["fields"]}
    assert client.put("/api/business/templates/sample", json=changed, headers=headers).status_code == 409
    assert client.get("/api/business/template-requests/missing_key_123456").status_code == 404
    assert client.post(
        "/api/business/templates/sample/disable", headers={"Idempotency-Key": "short", "If-Match": '"2"'},
    ).status_code == 422


def test_template_api_rejects_missing_and_stale_version_preconditions(tmp_path: Path) -> None:
    store = _store(tmp_path)
    client = TestClient(create_app(workflow=Mock(), store=store, evidence_reader=Mock(), evidence_writer=Mock()))
    store.create_template(code="sample", name="初版", fields=(TemplateField("title", "标题"),))
    body = {"name": "新版", "fields": [{"code": "title", "label": "标题"}]}
    assert client.put("/api/business/templates/sample", json=body).status_code == 422
    assert client.post("/api/business/templates/sample/disable").status_code == 422
    assert client.put("/api/business/templates/sample", json=body, headers={"If-Match": '"0"'}).status_code == 422
    accepted = client.put("/api/business/templates/sample", json=body, headers={"If-Match": '"1"'})
    assert accepted.status_code == 200 and accepted.json()["version"] == 2
    stale = client.put("/api/business/templates/sample", json=body, headers={"If-Match": '"1"'})
    assert stale.status_code == 409
    assert client.post("/api/business/templates/sample/disable", headers={"If-Match": '"1"'}).status_code == 409
    assert store.get_template("sample").version == 2 and store.get_template("sample").enabled


def test_document_freezes_selected_template_version_without_user_identity(tmp_path: Path) -> None:
    store = _store(tmp_path)
    uploads = ImmutableUploadStore(tmp_path, max_bytes=1024)
    first = store.create_template(code="my_report", name="我的报告", fields=(TemplateField("title", "标题"),))
    old_document = store.create_document(
        uploads.store(io.BytesIO(b"<h1>First</h1>"), filename="first.html"),
        original_name="first.html", template_code="my_report",
    )
    store.update_template("my_report", name="二版", fields=(TemplateField("date", "日期", "date"),))
    new_document = store.create_document(
        uploads.store(io.BytesIO(b"<h1>Second</h1>"), filename="second.html"),
        original_name="second.html", template_code="my_report",
    )
    assert old_document.template_code == new_document.template_code == first.code
    assert old_document.template_version == 1
    assert new_document.template_version == 2
    assert store.get_document(old_document.id) == old_document
    assert not hasattr(old_document, "owner_id")
    store.disable_template("my_report")
    with pytest.raises(BusinessStoreError, match="disabled"):
        store.create_document(
            uploads.store(io.BytesIO(b"<h1>Third</h1>"), filename="third.html"),
            original_name="third.html", template_code="my_report",
        )
