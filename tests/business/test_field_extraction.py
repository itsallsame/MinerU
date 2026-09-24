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
from mineru.doclib.locators import block_ref
from mineru.doclib.types import ContentRequestScope, DocContentResponse, ParseBlockSummary, ParseStructureResponse


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
    doclib.read_parse_structure.return_value = ParseStructureResponse(
        sha256=upload.sha256, short_id=upload.sha256[:12], tier="flash", page_no=1, blocks=[],
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
    doclib.read_parse_structure.assert_not_called()


def test_bold_label_with_inner_colon_keeps_existing_field_evidence(tmp_path: Path) -> None:
    store, _doclib, extractor, revision_id = _fixture(
        tmp_path, content="<!-- page 1 -->\n\n**标题：** 年度通知", template_code="official_document"
    )
    extractor.enqueue(revision_id)
    run = extractor.process_next()
    assert run is not None and run.status == "done"
    candidates = [item for item in store.list_field_candidates(run.id) if item.field_code == "title"]
    assert [(item.value, item.method) for item in candidates] == [("年度通知", "label_rule")]
    assert store.get_evidence(candidates[0].evidence_id).revision_id == revision_id


def test_native_document_title_proposes_block_bound_unconfirmed_candidate(tmp_path: Path) -> None:
    store, doclib, extractor, revision_id = _fixture(tmp_path, content="<!-- page 1 -->\n\n正文内容")
    revision = store.get_revision(revision_id)
    assert revision is not None
    title_locator = block_ref(revision.short_id, revision.tier, 1, 1)
    doclib.read_parse_structure.return_value = ParseStructureResponse(
        sha256=revision.sha256, short_id=revision.short_id, tier=revision.tier, page_no=1,
        blocks=[ParseBlockSummary(
            type="doc_title", block_no=1, locator=title_locator, path=[0],
            preview="预览可能截断，不可作为候选", bbox=(1, 2, 3, 4),
        )],
    )
    page_response = doclib.read_parse_content.return_value
    title_response = page_response.model_copy(update={
        "content": "<!-- page 1 -->\n\n# 年度工作报告",
        "request_scope": ContentRequestScope(locator=title_locator),
    })
    doclib.read_parse_content.side_effect = lambda parse_id, locator, *, limit: (
        title_response if locator == title_locator else page_response
    )
    extractor.enqueue(revision_id)
    run = extractor.process_next()
    assert run is not None and run.status == "done"
    candidates = store.list_field_candidates(run.id)
    assert [(item.field_code, item.value, item.method) for item in candidates] == [
        ("title", "年度工作报告", "native_doc_title")
    ]
    assert store.get_evidence(candidates[0].evidence_id).locator == title_locator
    assert store.list_quality_issues(run.id) == ()
    doclib.read_parse_content.assert_any_call(7, title_locator, limit=2000)
    doclib.read_parse_content.assert_any_call(7, title_locator, limit=30000)


@pytest.mark.parametrize("bad_structure", ["identity", "path", "locator"])
def test_native_title_rejects_mismatched_historical_structure(tmp_path: Path, bad_structure: str) -> None:
    store, doclib, extractor, revision_id = _fixture(tmp_path, content="<!-- page 1 -->\n\n正文")
    revision = store.get_revision(revision_id)
    assert revision is not None
    title_locator = block_ref(revision.short_id, revision.tier, 1, 1)
    block = ParseBlockSummary(type="doc_title", block_no=1, locator=title_locator, path=[0], preview="标题")
    structure = ParseStructureResponse(
        sha256=revision.sha256, short_id=revision.short_id, tier=revision.tier, page_no=1, blocks=[block],
    )
    if bad_structure == "identity":
        structure = structure.model_copy(update={"sha256": "0" * 64})
    elif bad_structure == "path":
        structure = structure.model_copy(update={"blocks": [block.model_copy(update={"path": [1]})]})
    else:
        structure = structure.model_copy(update={
            "blocks": [block.model_copy(update={"locator": block_ref(revision.short_id, revision.tier, 1, 2)})],
        })
    doclib.read_parse_structure.return_value = structure
    extractor.enqueue(revision_id)
    run = extractor.process_next()
    assert run is not None and run.status == "failed"
    assert run.error_code == "historical_content_unavailable"
    assert store.list_field_candidates(run.id) == ()


@pytest.mark.parametrize("block_text,truncated", [
    ("<!-- page 1 -->\n\n# 标题\n第二行", False),
    ('<!-- page 1 -->\n\n<a id="other-anchor"></a>\n# 标题', False),
    ("<!-- page 1 -->\n\n# 标题", True),
])
def test_native_title_never_uses_preview_or_incomplete_content(
    tmp_path: Path, block_text: str, truncated: bool,
) -> None:
    store, doclib, extractor, revision_id = _fixture(tmp_path, content="<!-- page 1 -->\n\n正文")
    revision = store.get_revision(revision_id)
    assert revision is not None
    title_locator = block_ref(revision.short_id, revision.tier, 1, 1)
    doclib.read_parse_structure.return_value = ParseStructureResponse(
        sha256=revision.sha256, short_id=revision.short_id, tier=revision.tier, page_no=1,
        blocks=[ParseBlockSummary(
            type="doc_title", block_no=1, locator=title_locator, path=[0], preview="预览标题",
        )],
    )
    page_response = doclib.read_parse_content.return_value
    block_response = page_response.model_copy(update={
        "content": block_text, "truncated": truncated,
        "request_scope": ContentRequestScope(locator=title_locator),
    })
    doclib.read_parse_content.side_effect = lambda parse_id, locator, *, limit: (
        block_response if locator == title_locator else page_response
    )
    extractor.enqueue(revision_id)
    run = extractor.process_next()
    assert run is not None
    assert run.status == ("failed" if truncated else "done")
    assert store.list_field_candidates(run.id) == ()


@pytest.mark.parametrize("content,expected", [
    ("<!-- page 1 -->\n\n## 摘要\n\n本文研究离线解析。\n\n## 引言", ("本文研究离线解析。", "section_heading")),
    (
        "<!-- page 1 -->\n\n## Abstract\n\nFirst line.\nSecond line.\n\n## Intro",
        ("First line.\nSecond line.", "section_heading"),
    ),
    ("<!-- page 1 -->\n\n摘要：显式摘要\n\n## 摘要\n\n章节摘要", ("显式摘要", "label_rule")),
    ("<!-- page 1 -->\n\n## 摘要\n\n## 引言", None),
    ("<!-- page 1 -->\n\n## 摘要\n\n<a id=\"html-12345678\"></a>\n## 引言", None),
])
def test_abstract_heading_candidate_is_conservative_and_source_bound(
    tmp_path: Path, content: str, expected: tuple[str, str] | None,
) -> None:
    store, _doclib, extractor, revision_id = _fixture(tmp_path, content=content, template_code="paper")
    extractor.enqueue(revision_id)
    run = extractor.process_next()
    assert run is not None and run.status == "done"
    abstract_candidates = [item for item in store.list_field_candidates(run.id) if item.field_code == "abstract"]
    assert [(item.value, item.method) for item in abstract_candidates] == ([expected] if expected else [])
    if expected:
        evidence = store.get_evidence(abstract_candidates[0].evidence_id)
        assert evidence.revision_id == revision_id and expected[0] in evidence.snippet


@pytest.mark.parametrize("content,expected", [
    ("<!-- page 1 -->\n\n关键词：离线解析；证据", "离线解析；证据"),
    ("<!-- page 1 -->\n\nKeywords: document parsing; evidence", "document parsing; evidence"),
    ("<!-- page 1 -->\n\nKEYWORDS: document parsing", "document parsing"),
    ("<!-- page 1 -->\n\n**Keywords:** document parsing, evidence", "document parsing, evidence"),
    ("<!-- page 1 -->\n\n**关键词**：离线解析、证据", "离线解析、证据"),
    ("<!-- page 1 -->\n\n## Keywords\n\ndocument parsing", None),
    ("<!-- page 1 -->\n\n本文的 Keywords: 只是正文。", None),
])
def test_explicit_keyword_labels_make_unconfirmed_source_bound_candidates(
    tmp_path: Path, content: str, expected: str | None,
) -> None:
    store, _doclib, extractor, revision_id = _fixture(tmp_path, content=content, template_code="paper")
    extractor.enqueue(revision_id)
    run = extractor.process_next()
    assert run is not None and run.status == "done"
    candidates = [item for item in store.list_field_candidates(run.id) if item.field_code == "keywords"]
    assert [(item.value, item.method) for item in candidates] == ([(expected, "label_rule")] if expected else [])
    if expected:
        evidence = store.get_evidence(candidates[0].evidence_id)
        assert evidence.revision_id == revision_id and expected in evidence.snippet
        assert not hasattr(candidates[0], "confirmed")


@pytest.mark.parametrize("content,expected", [
    ("| 项目 | 内容 |\n| --- | --- |\n| 发文单位 | 办公室 |", "办公室"),
    ("| 项目 | 内容 |\n| :--- | ---: |\n| **发文单位** | 办公室 |", "办公室"),
    ("| 项目 | 内容 |\n| 发文单位 | 办公室 |", None),
    ("| 发文单位 | 办公室 |\n| --- | --- |", None),
    ("| 项目 | 内容 | 备注 |\n| --- | --- | --- |\n| 发文单位 | 办公室 | 草稿 |", None),
    ("| 项目 | 内容 |\n| --- | --- |\n| 其他单位 | 办公室 |", None),
])
def test_two_column_table_candidate_requires_explicit_data_row_and_frozen_evidence(
    tmp_path: Path, content: str, expected: str | None,
) -> None:
    store, _doclib, extractor, revision_id = _fixture(tmp_path, content=content)
    extractor.enqueue(revision_id)
    run = extractor.process_next()
    assert run is not None and run.status == "done"
    candidates = [item for item in store.list_field_candidates(run.id) if item.field_code == "issuer"]
    assert [(item.value, item.method) for item in candidates] == ([(expected, "table_row")] if expected else [])
    if expected:
        evidence = store.get_evidence(candidates[0].evidence_id)
        assert evidence.revision_id == revision_id and expected in evidence.snippet
        assert not hasattr(candidates[0], "confirmed")


def test_distinct_table_values_remain_unconfirmed_conflicting_candidates(tmp_path: Path) -> None:
    content = (
        "| 项目 | 内容 |\n| --- | --- |\n| 发文单位 | 办公室 |\n\n"
        "| 项目 | 内容 |\n| --- | --- |\n| 发文单位 | 委员会 |"
    )
    store, _doclib, extractor, revision_id = _fixture(tmp_path, content=content)
    extractor.enqueue(revision_id)
    run = extractor.process_next()
    assert run is not None and run.status == "done"
    assert {(item.value, item.method) for item in store.list_field_candidates(run.id) if item.field_code == "issuer"} == {
        ("办公室", "table_row"), ("委员会", "table_row"),
    }
    assert ("issuer", "conflicting_candidates", "blocking") in {
        (issue.field_code, issue.code, issue.severity) for issue in store.list_quality_issues(run.id)
    }


def test_same_page_label_and_table_value_is_one_preferred_candidate(tmp_path: Path) -> None:
    content = "发文单位：办公室\n\n| 项目 | 内容 |\n| --- | --- |\n| 发文单位 | 办公室 |"
    store, _doclib, extractor, revision_id = _fixture(tmp_path, content=content)
    extractor.enqueue(revision_id)
    run = extractor.process_next()
    assert run is not None and run.status == "done"
    assert [(item.value, item.method) for item in store.list_field_candidates(run.id) if item.field_code == "issuer"] == [
        ("办公室", "label_rule")
    ]


def test_multi_batch_revision_extracts_each_page_from_its_historical_batch(tmp_path: Path) -> None:
    store = BusinessStore(tmp_path / "business.sqlite3")
    store.initialize()
    upload = ImmutableUploadStore(tmp_path, max_bytes=1024).store(
        io.BytesIO(b"%PDF-1.7\nsource"), filename="source.pdf"
    )
    document = store.create_document(upload, original_name="source.pdf", template_code="official_document")
    short_id = upload.sha256[:7]
    parses = tuple(ParseInfo(
        id=parse_id, sha256=upload.sha256, short_id=short_id, tier="flash",
        page_range=str(page_no), status="done", privacy="local", created_at=1,
        updated_at=2, done_at=2,
    ) for parse_id, page_no in ((7, 1), (8, 2)))
    revision = store.add_completed_revision(document.id, parse=parses, producer_version="4.0.6")
    doclib = Mock(spec=DoclibInterface)
    doclib.get_parse.side_effect = lambda parse_id: parses[parse_id - 7]
    doclib.get_doc.return_value.page_count = 2

    def read(parse_id: int, locator: str, *, limit: int) -> DocContentResponse:
        assert parse_id == (7 if "/page:1" in locator else 8)
        return DocContentResponse(
            sha256=upload.sha256, short_id=short_id, tier="flash",
            content="标题：年度通知" if parse_id == 7 else "发文单位：办公室",
            request_scope=ContentRequestScope(locator=locator),
        )

    doclib.read_parse_content.side_effect = read
    extractor = FieldExtraction(store=store, doclib=doclib, evidence_writer=EvidenceWriter(store=store, doclib=doclib))
    extractor.enqueue(revision.id)
    run = extractor.process_next()
    assert run is not None and run.status == "done"
    assert {(item.field_code, item.value) for item in store.list_field_candidates(run.id)} == {
        ("title", "年度通知"), ("issuer", "办公室"),
    }
    assert store.list_quality_issues(run.id) == ()
    assert {call.args[0] for call in doclib.read_parse_content.call_args_list} == {7, 8}


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
