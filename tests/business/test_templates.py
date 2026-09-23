"""Open template definitions are versioned contracts, not permission records."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from mineru.business.api import create_app
from mineru.business.domain import TemplateField
from mineru.business.store import BusinessStore, BusinessStoreError


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
    updated = client.put("/api/business/templates/sample", json={
        "name": "样本二版", "fields": [{"code": "date", "label": "日期", "type": "date"}]
    })
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert client.get("/api/business/templates/sample?version=1").json()["fields"][0]["code"] == "title"
    assert client.get("/api/business/templates/sample?version=3").status_code == 404
    assert client.post("/api/business/templates/sample/disable").json()["enabled"] is False
