"""Field candidates are provisional and always tied to historical source evidence."""

from __future__ import annotations

import io
import time
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from mineru.business.api import create_app
from mineru.business.documents import ImmutableUploadStore
from mineru.business.domain import TemplateField
from mineru.business.services import EvidenceReader, EvidenceWriter, ExtractionWorker, FieldExtraction
from mineru.business.store import BusinessStore, BusinessStoreError
from mineru.doclib import DoclibInterface, ParseInfo
from mineru.doclib.types import ContentRequestScope, DocContentResponse


def _fixture(
    tmp_path: Path, *, content: str, template_code: str | None = "official_document"
) -> tuple[BusinessStore, Mock, FieldExtraction, str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = BusinessStore(tmp_path / "business.sqlite3")
    store.initialize()
    if template_code == "custom_report":
        store.create_template(
            code="custom_report", name="自定义报告", fields=(TemplateField("title", "旧标题", required=True),)
        )
    upload = ImmutableUploadStore(tmp_path, max_bytes=1024).store(
        io.BytesIO(b"<h1>Source</h1>"), filename="source.html"
    )
    document = store.create_document(upload, original_name="source.html", template_code=template_code)
    parse = ParseInfo(
        id=7, sha256=upload.sha256, short_id=upload.sha256[:12], tier="flash",
        page_range="1", status="done", privacy="local", created_at=1, updated_at=2, done_at=2,
    )
    revision = store.add_completed_revision(document.id, parse=parse, producer_version="4.0.6")
    locator = f"doc:{upload.sha256[:12]}/tier:flash/page:1"
    doclib = Mock(spec=DoclibInterface)
    doclib.get_parse.return_value = parse
    doclib.get_doc.return_value.page_count = 1
    doclib.read_parse_content.return_value = DocContentResponse(
        sha256=upload.sha256, short_id=upload.sha256[:12], tier="flash", content=content,
        request_scope=ContentRequestScope(locator=locator),
    )
    writer = EvidenceWriter(store=store, doclib=doclib)
    return store, doclib, FieldExtraction(store=store, doclib=doclib, evidence_writer=writer), revision.id


def test_explicit_labels_create_unconfirmed_candidates_and_missing_issue(tmp_path: Path) -> None:
    store, doclib, extractor, revision_id = _fixture(
        tmp_path, content="<!-- page 1 -->\n\n标题：2026年通知\n\n发文单位：办公室"
    )
    queued = extractor.enqueue(revision_id)
    assert queued.status == "queued"
    run = extractor.process_next()
    assert run is not None
    assert run.status == "done"
    assert run.template_code == "official_document" and run.template_version == 1
    candidates = store.list_field_candidates(run.id)
    assert {(item.field_code, item.value) for item in candidates} == {
        ("title", "2026年通知"), ("issuer", "办公室")
    }
    assert store.list_quality_issues(run.id) == ()
    for candidate in candidates:
        evidence = store.get_evidence(candidate.evidence_id)
        assert evidence.revision_id == revision_id
        assert candidate.value in evidence.snippet
        assert candidate.method == "label_rule"
        assert not hasattr(candidate, "confirmed")
    doclib.read_parse_content.assert_any_call(7, store.get_evidence(candidates[0].evidence_id).locator, limit=30000)


def test_missing_required_and_conflicting_values_are_blocking(tmp_path: Path) -> None:
    store, _doclib, extractor, revision_id = _fixture(
        tmp_path, content="标题：第一版\n标题：第二版\n发文单位：办公室"
    )
    extractor.enqueue(revision_id)
    run = extractor.process_next()
    assert run is not None
    assert run.status == "done"
    issues = store.list_quality_issues(run.id)
    assert [(issue.field_code, issue.code, issue.severity) for issue in issues] == [
        ("title", "conflicting_candidates", "blocking")
    ]

    store2, _doclib2, extractor2, revision_id2 = _fixture(
        tmp_path / "missing", content="发文单位：办公室"
    )
    extractor2.enqueue(revision_id2)
    missing_run = extractor2.process_next()
    assert missing_run is not None
    assert [(issue.field_code, issue.code) for issue in store2.list_quality_issues(missing_run.id)] == [
        ("title", "required_missing")
    ]


def test_partial_parse_never_claims_required_field_missing(tmp_path: Path) -> None:
    store, doclib, extractor, revision_id = _fixture(tmp_path, content="发文单位：办公室")
    doclib.get_doc.return_value.page_count = 2
    extractor.enqueue(revision_id)
    run = extractor.process_next()
    assert run is not None
    assert run.status == "done"
    assert [(issue.field_code, issue.code) for issue in store.list_quality_issues(run.id)] == [
        (None, "coverage_incomplete")
    ]


def test_extraction_uses_frozen_template_and_rejects_non_source_candidates(tmp_path: Path) -> None:
    store, _doclib, extractor, revision_id = _fixture(
        tmp_path, content="旧标题：版本一", template_code="custom_report"
    )
    store.update_template("custom_report", name="二版", fields=(TemplateField("title", "新标题", required=True),))
    extractor.enqueue(revision_id)
    run = extractor.process_next()
    assert run is not None
    assert run.status == "done" and run.template_version == 1
    assert [candidate.value for candidate in store.list_field_candidates(run.id)] == ["版本一"]
    assert store.get_template("custom_report").version == 2


def test_extraction_failure_is_persisted_and_not_exposed_as_result(tmp_path: Path) -> None:
    store, doclib, extractor, revision_id = _fixture(tmp_path, content="标题：不完整")
    doclib.read_parse_content.return_value = doclib.read_parse_content.return_value.model_copy(
        update={"truncated": True}
    )
    extractor.enqueue(revision_id)
    run = extractor.process_next()
    assert run is not None
    assert run.status == "failed" and run.error_code == "historical_content_incomplete"
    assert store.list_quality_issues(run.id) == ()

    writer = EvidenceWriter(store=store, doclib=doclib)
    client = TestClient(create_app(
        workflow=Mock(), store=store, evidence_reader=EvidenceReader(store=store, doclib=doclib),
        evidence_writer=writer, field_extraction=extractor,
    ))
    response = client.get(f"/api/business/extractions/{run.id}")
    assert response.status_code == 200
    assert response.json()["run"]["status"] == "failed"
    assert response.json()["candidates"] == [] and response.json()["issues"] == []
    assert client.get("/api/business/extractions/missing").status_code == 404


def test_open_extraction_api_keeps_doclib_parse_id_private(tmp_path: Path) -> None:
    store, doclib, extractor, revision_id = _fixture(tmp_path, content="标题：公开接口")
    writer = EvidenceWriter(store=store, doclib=doclib)
    client = TestClient(create_app(
        workflow=Mock(), store=store, evidence_reader=EvidenceReader(store=store, doclib=doclib),
        evidence_writer=writer, field_extraction=extractor,
    ))
    created = client.post(f"/api/business/revisions/{revision_id}/extractions")
    assert created.status_code == 202
    assert created.json()["status"] == "queued"
    assert "claim_token" not in created.json()
    assert "doclib_parse_id" not in created.json()
    run_id = created.json()["id"]
    assert extractor.process_next().status == "done"
    detail = client.get(f"/api/business/extractions/{run_id}")
    assert detail.json()["candidates"][0]["value"] == "公开接口"
    assert detail.json()["candidates"][0]["evidence_id"]
    assert len(client.get(f"/api/business/revisions/{revision_id}/extractions").json()) == 1
    assert client.post("/api/business/revisions/missing/extractions").status_code == 404


def test_api_lifespan_worker_processes_queued_run_without_blocking_post(tmp_path: Path) -> None:
    store, doclib, extractor, revision_id = _fixture(tmp_path, content="标题：后台完成")
    writer = EvidenceWriter(store=store, doclib=doclib)
    worker = ExtractionWorker(extractor, poll_seconds=0.01)
    app = create_app(
        workflow=Mock(), store=store, evidence_reader=EvidenceReader(store=store, doclib=doclib),
        evidence_writer=writer, field_extraction=extractor, extraction_worker=worker,
    )
    with TestClient(app) as client:
        created = client.post(f"/api/business/revisions/{revision_id}/extractions")
        assert created.status_code == 202
        run_id = created.json()["id"]
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            result = client.get(f"/api/business/extractions/{run_id}").json()
            if result["run"]["status"] == "done":
                break
            time.sleep(0.01)
        else:
            pytest.fail("Background extraction did not finish")
        assert result["candidates"][0]["value"] == "后台完成"


def test_store_rejects_candidate_without_same_revision_evidence(tmp_path: Path) -> None:
    store, _doclib, _extractor, revision_id = _fixture(tmp_path, content="标题：真实标题")
    store.enqueue_extraction(revision_id)
    run = store.claim_next_extraction()
    assert run is not None and run.claim_token is not None
    with pytest.raises(BusinessStoreError, match="same revision"):
        store.add_field_candidate(
            run.id, claim_token=run.claim_token, field_code="title", value="伪造", evidence_id="missing"
        )
    locator = f"doc:{store.get_revision(revision_id).short_id}/tier:flash/page:1"
    evidence = store.capture_evidence(revision_id, locator=locator, snippet="标题：真实标题")
    with pytest.raises(BusinessStoreError, match="absent"):
        store.add_field_candidate(
            run.id, claim_token=run.claim_token, field_code="title", value="伪造", evidence_id=evidence.id
        )
    with pytest.raises(BusinessStoreError, match="frozen template"):
        store.add_field_candidate(
            run.id, claim_token=run.claim_token, field_code="not_a_field", value="真实标题",
            evidence_id=evidence.id,
        )
