"""No machine candidate becomes a confirmed result without an explicit review decision."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from mineru.business.api import create_app
from mineru.business.documents import ImmutableUploadStore
from mineru.business.store import BusinessStore, BusinessStoreError
from mineru.doclib import ParseInfo


def _run(tmp_path: Path, *, snippet: str, candidate_values: tuple[str, ...] = ()) -> tuple[BusinessStore, str, str]:
    store = BusinessStore(tmp_path / "business.sqlite3")
    store.initialize()
    upload = ImmutableUploadStore(tmp_path, max_bytes=1024).store(
        io.BytesIO(b"<h1>Source</h1>"), filename="notice.html"
    )
    document = store.create_document(upload, original_name="notice.html", template_code="official_document")
    parse = ParseInfo(
        id=7, sha256=upload.sha256, short_id=upload.sha256[:12], tier="flash", page_range="1",
        status="done", privacy="local", created_at=1, updated_at=2, done_at=2,
    )
    revision = store.add_completed_revision(document.id, parse=parse, producer_version="4.0.6")
    evidence = store.capture_evidence(
        revision.id, locator=f"doc:{upload.sha256[:12]}/tier:flash/page:1", snippet=snippet
    )
    store.enqueue_extraction(revision.id)
    run = store.claim_next_extraction()
    assert run is not None and run.claim_token is not None
    for value in candidate_values:
        store.add_field_candidate(
            run.id, claim_token=run.claim_token, field_code="title", value=value, evidence_id=evidence.id
        )
    store.finish_extraction(run.id, claim_token=run.claim_token, complete_coverage=True)
    return store, run.id, evidence.id


def test_candidate_requires_explicit_decision_before_confirmation(tmp_path: Path) -> None:
    store, run_id, evidence_id = _run(tmp_path, snippet="标题：年度通知", candidate_values=("年度通知",))
    with pytest.raises(BusinessStoreError, match="explicit review"):
        store.confirm_result(run_id, source="web")
    decision = store.decide_field(
        run_id, field_code="title", value="年度通知", evidence_id=evidence_id, source="web"
    )
    assert decision.basis == "candidate_acceptance" and decision.previous_value is None
    result = store.confirm_result(run_id, source="web")
    assert result.version == 1
    assert result.fields[0].value == "年度通知"
    assert result.fields[0].evidence_id == evidence_id
    assert result.fields[0].decision_id == decision.id
    assert store.get_confirmed_result(result.id) == result
    assert [event.action for event in store.list_audit_events(run_id)] == ["field_decided", "result_confirmed"]
    assert not hasattr(decision, "user_id")
    with pytest.raises(BusinessStoreError, match="No review changes"):
        store.confirm_result(run_id, source="web")


def test_quality_stats_count_workflow_records_not_accuracy(tmp_path: Path) -> None:
    store, run_id, evidence_id = _run(tmp_path, snippet="标题：年度通知", candidate_values=("年度通知",))
    before = store.quality_stats()
    assert before["documents"] == before["revisions"] == before["extraction_done"] == 1
    assert before["confirmed_runs"] == before["result_versions"] == 0
    assert before["open_issues"] == 0
    store.decide_field(run_id, field_code="title", value="年度通知", evidence_id=evidence_id, source="web")
    store.confirm_result(run_id, source="web")
    store.decide_field(
        run_id, field_code="title", value="年度通知（修订）", evidence_id=evidence_id,
        source="api", reason="明确复核修订",
    )
    store.confirm_result(run_id, source="api")
    after = store.quality_stats()
    assert after["confirmed_runs"] == 1
    assert after["result_versions"] == 2
    assert after["documents"] == after["revisions"] == after["extraction_done"] == 1
    assert "accuracy" not in after


def test_global_audit_cursor_is_stable_and_open_api_has_no_identity(tmp_path: Path) -> None:
    store, run_id, evidence_id = _run(tmp_path, snippet="标题：年度通知", candidate_values=("年度通知",))
    store.decide_field(run_id, field_code="title", value="年度通知", evidence_id=evidence_id, source="web")
    store.confirm_result(run_id, source="skill")
    newest = store.audit_page(limit=1)
    assert len(newest.items) == 1 and newest.next_before is not None
    assert newest.items[0].event.action == "result_confirmed"
    assert newest.items[0].document_name == "notice.html"
    assert newest.items[0].event.source == "skill"
    assert not hasattr(newest.items[0].event, "user_id")
    store.decide_field(
        run_id, field_code="title", value="通知（修订）", evidence_id=evidence_id,
        source="api", reason="复核后修订",
    )
    older = store.audit_page(limit=1, before=newest.next_before)
    assert [item.event.action for item in older.items] == ["field_decided"]
    assert older.next_before is None
    assert [item.event.action for item in store.audit_page().items] == [
        "field_decided", "result_confirmed", "field_decided",
    ]
    with pytest.raises(BusinessStoreError, match="cursor not found"):
        store.audit_page(before="missing")
    with pytest.raises(BusinessStoreError, match="limit"):
        store.audit_page(limit=101)

    client = TestClient(create_app(
        workflow=Mock(), store=store, evidence_reader=Mock(), evidence_writer=Mock(),
    ))
    response = client.get("/api/business/audit?limit=1")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["document_name"] == "notice.html"
    assert item["event"]["source"] == "api"
    assert "user_id" not in item and "user_id" not in item["event"]
    assert client.get("/api/business/audit?limit=101").status_code == 422
    assert client.get("/api/business/audit?before=missing").status_code == 404


def test_manual_correction_keeps_old_confirmed_result_immutable(tmp_path: Path) -> None:
    store, run_id, evidence_id = _run(tmp_path, snippet="标题：年度通知", candidate_values=("年度通知",))
    store.decide_field(run_id, field_code="title", value="年度通知", evidence_id=evidence_id, source="web")
    first = store.confirm_result(run_id, source="web")
    with pytest.raises(BusinessStoreError, match="requires a reason"):
        store.decide_field(run_id, field_code="title", value="规范化通知", evidence_id=evidence_id, source="api")
    second_decision = store.decide_field(
        run_id, field_code="title", value="规范化通知", evidence_id=evidence_id,
        source="api", reason="按原文标题规范化",
    )
    assert second_decision.previous_value == "年度通知"
    assert second_decision.basis == "manual_correction"
    second = store.confirm_result(run_id, source="api")
    assert second.version == 2 and second.fields[0].value == "规范化通知"
    assert first.fields[0].value == store.get_confirmed_result(first.id).fields[0].value == "年度通知"
    assert [result.version for result in store.list_confirmed_results(run_id)] == [2, 1]
    assert len(store.list_field_decisions(run_id)) == 2
    reopened = BusinessStore(tmp_path / "business.sqlite3")
    reopened.initialize()
    assert reopened.list_confirmed_results(run_id) == (second, first)
    assert reopened.list_field_decisions(run_id) == store.list_field_decisions(run_id)
    assert [event.old_value for event in store.list_audit_events(run_id) if event.action == "field_decided"] == [
        None, "年度通知"
    ]


def test_required_missing_and_conflict_block_until_reviewed(tmp_path: Path) -> None:
    store, run_id, evidence_id = _run(tmp_path, snippet="原文没有显式标题")
    assert store.quality_stats()["open_issues"] == 1
    issue = store.list_quality_issues(run_id)[0]
    assert issue.code == "required_missing"
    with pytest.raises(BusinessStoreError, match="cannot be ignored"):
        store.resolve_issue(issue.id, status="ignored", source="web", reason="跳过")
    with pytest.raises(BusinessStoreError, match="requires a field decision"):
        store.resolve_issue(issue.id, status="resolved", source="web", reason="已检查")
    store.decide_field(
        run_id, field_code="title", value="人工识别标题", evidence_id=evidence_id,
        source="web", reason="标题在原文页上但没有标签",
    )
    with pytest.raises(BusinessStoreError, match="Blocking quality issues"):
        store.confirm_result(run_id, source="web")
    resolution = store.resolve_issue(issue.id, status="resolved", source="web", reason="已结合原文复核")
    assert store.quality_stats()["open_issues"] == 0
    assert resolution.previous_status == "open"
    assert store.list_quality_issues(run_id)[0].status == "resolved"
    assert store.confirm_result(run_id, source="web").version == 1
    with pytest.raises(BusinessStoreError, match="not open"):
        store.resolve_issue(issue.id, status="ignored", source="web", reason="重复")

    other = tmp_path / "conflict"
    other.mkdir()
    store2, run_id2, evidence_id2 = _run(
        other, snippet="标题：甲\n标题：乙", candidate_values=("甲", "乙")
    )
    conflict = store2.list_quality_issues(run_id2)[0]
    assert conflict.code == "conflicting_candidates"
    store2.decide_field(run_id2, field_code="title", value="乙", evidence_id=evidence_id2, source="web")
    store2.resolve_issue(conflict.id, status="resolved", source="web", reason="以第二处为准")
    assert store2.confirm_result(run_id2, source="web").fields[0].value == "乙"


def test_incomplete_coverage_cannot_be_waived(tmp_path: Path) -> None:
    store = BusinessStore(tmp_path / "business.sqlite3")
    store.initialize()
    upload = ImmutableUploadStore(tmp_path, max_bytes=1024).store(io.BytesIO(b"source"), filename="source.html")
    document = store.create_document(upload, original_name="source.html", template_code="official_document")
    parse = ParseInfo(
        id=9, sha256=upload.sha256, short_id=upload.sha256[:12], tier="flash", page_range="1",
        status="done", privacy="local", created_at=1, updated_at=2, done_at=2,
    )
    revision = store.add_completed_revision(document.id, parse=parse, producer_version="4.0.6")
    store.enqueue_extraction(revision.id)
    run = store.claim_next_extraction()
    assert run is not None and run.claim_token is not None
    store.finish_extraction(run.id, claim_token=run.claim_token, complete_coverage=False)
    issue = store.list_quality_issues(run.id)[0]
    assert issue.code == "coverage_incomplete"
    with pytest.raises(BusinessStoreError, match="cannot be waived"):
        store.resolve_issue(issue.id, status="ignored", source="web", reason="先通过")
    with pytest.raises(BusinessStoreError, match="Blocking quality issues"):
        store.confirm_result(run.id, source="web")


def test_cross_revision_evidence_and_unknown_review_source_are_rejected(tmp_path: Path) -> None:
    store, run_id, evidence_id = _run(tmp_path, snippet="标题：年度通知", candidate_values=("年度通知",))
    with pytest.raises(BusinessStoreError, match="Review source"):
        store.decide_field(run_id, field_code="title", value="年度通知", evidence_id=evidence_id, source="person-1")
    with pytest.raises(BusinessStoreError, match="same revision"):
        store.decide_field(run_id, field_code="title", value="年度通知", evidence_id="missing", source="web")


def test_open_review_api_exposes_only_confirmed_result_versions(tmp_path: Path) -> None:
    store, run_id, evidence_id = _run(tmp_path, snippet="标题：年度通知", candidate_values=("年度通知",))
    client = TestClient(create_app(
        workflow=Mock(), store=store, evidence_reader=Mock(), evidence_writer=Mock()
    ))
    assert client.post(f"/api/business/extractions/{run_id}/confirm", json={"source": "web"}).status_code == 409
    decided = client.post(
        f"/api/business/extractions/{run_id}/fields/title/decisions",
        json={"value": "年度通知", "evidence_id": evidence_id, "source": "web"},
    )
    assert decided.status_code == 201
    assert decided.json()["basis"] == "candidate_acceptance"
    assert "user_id" not in decided.json()
    assert client.get(f"/api/business/extractions/{run_id}/decisions").json()[0]["id"] == decided.json()["id"]
    confirmed = client.post(f"/api/business/extractions/{run_id}/confirm", json={"source": "web"})
    assert confirmed.status_code == 201
    result_id = confirmed.json()["id"]
    assert confirmed.json()["fields"][0]["value"] == "年度通知"
    assert "doclib_parse_id" not in confirmed.json()
    assert client.get(f"/api/business/results/{result_id}").json() == confirmed.json()
    assert client.get(f"/api/business/extractions/{run_id}/results").json()[0]["id"] == result_id
    assert [event["action"] for event in client.get(f"/api/business/extractions/{run_id}/audit").json()] == [
        "field_decided", "result_confirmed"
    ]
    assert client.get("/api/business/results/missing").status_code == 404


def test_open_issue_resolution_api_requires_reviewed_field(tmp_path: Path) -> None:
    store, run_id, evidence_id = _run(tmp_path, snippet="本页标题没有标签")
    issue_id = store.list_quality_issues(run_id)[0].id
    client = TestClient(create_app(
        workflow=Mock(), store=store, evidence_reader=Mock(), evidence_writer=Mock()
    ))
    path = f"/api/business/issues/{issue_id}/resolutions"
    request = {"status": "resolved", "source": "web", "reason": "已根据原文复核"}
    assert client.post(path, json=request).status_code == 409
    client.post(
        f"/api/business/extractions/{run_id}/fields/title/decisions",
        json={
            "value": "人工识别标题", "evidence_id": evidence_id,
            "source": "web", "reason": "原文有标题但无标签",
        },
    )
    resolved = client.post(path, json=request)
    assert resolved.status_code == 201
    assert resolved.json()["status"] == "resolved"
    assert client.get(f"/api/business/extractions/{run_id}/resolutions").json()[0]["issue_id"] == issue_id
    assert client.get(f"/api/business/extractions/{run_id}").json()["issues"][0]["status"] == "resolved"
    assert client.post(f"/api/business/extractions/{run_id}/confirm", json={"source": "web"}).status_code == 201
