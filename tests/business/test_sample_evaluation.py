"""Four-class evaluation reports metrics without treating fixtures as production acceptance."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from mineru.business.api import create_app
from mineru.business.documents import ImmutableUploadStore
from mineru.business.services import EvidenceReader, EvidenceWriter, FieldExtraction
from mineru.business.store import BusinessStore
from mineru.doclib import DoclibInterface, ParseInfo
from mineru.doclib.types import ContentRequestScope, DocContentResponse


def _module() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "scripts" / "evaluate_business_samples.py"
    spec = importlib.util.spec_from_file_location("evaluate_business_samples", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _suite(root: Path) -> Path:
    cases = []
    for category in ("official_document", "paper", "research_report", "newspaper"):
        source = root / f"{category}.txt"
        source.write_text(f"Synthetic {category} source", encoding="utf-8")
        cases.append({
            "id": category, "category": category, "source": source.name,
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "document_id": f"doc-{category}", "revision_id": f"rev-{category}", "run_id": f"run-{category}",
            "expected_fields": [{"code": "title", "value": f"Gold {category}",
                                 "evidence_quote": f"Gold {category}", "page_no": 1}],
        })
    cases[0]["expected_headings"] = [{"title": "Introduction", "level": 1, "page_no": 1}]
    cases[0]["tags"] = ["seal_watermark"]
    path = root / "suite.json"
    path.write_text(json.dumps({"schema": 1, "cases": cases}), encoding="utf-8")
    return path


def _get(path: str) -> Any:
    module = path.split("/")
    category = module[-1].replace("doc-", "").replace("rev-", "").replace("run-", "")
    if path.startswith("/documents/") and path.endswith("/revisions"):
        category = module[-2].replace("doc-", "")
        return [{"id": f"rev-{category}", "document_id": f"doc-{category}"}]
    if path.startswith("/documents/"):
        source = f"Synthetic {category} source".encode()
        return {"id": f"doc-{category}", "sha256": hashlib.sha256(source).hexdigest(), "template_code": category}
    if path.startswith("/extractions/"):
        return {
            "run": {"id": f"run-{category}", "revision_id": f"rev-{category}",
                    "template_code": category, "status": "done"},
            "candidates": [{"field_code": "title", "value": f"Gold {category}",
                            "evidence_id": f"ev-{category}"}],
            "issues": [],
        }
    if path.endswith("/evidence"):
        category = module[-2].replace("rev-", "")
        snippet = f"Gold {category} frozen"
        return [{"id": f"ev-{category}", "revision_id": f"rev-{category}",
                 "document_id": f"doc-{category}", "snippet": snippet,
                 "snippet_sha256": hashlib.sha256(snippet.encode()).hexdigest(), "page_no": 1}]
    if path.endswith("/outline"):
        return {"revision_id": "rev-official_document", "items": [], "scanned_pages": 1, "next_page": 2}
    if path.endswith("/outline?start_page=2"):
        return {"revision_id": "rev-official_document",
                "items": [{"title": "Introduction", "level": 1, "page_no": 1}],
                "scanned_pages": 1, "next_page": None}
    raise AssertionError(path)


def test_four_class_suite_reports_exact_metrics_without_raw_annotations(tmp_path: Path) -> None:
    module = _module()
    cases = module.load_suite(_suite(tmp_path))
    report = module.evaluate_suite(cases, _get, "Mac synthetic contract")
    assert report["case_count"] == 4 and len(report["classes"]) == 4
    assert report["aggregate"]["expected_fields"] == 4
    assert report["aggregate"]["matched_fields"] == 4
    assert report["aggregate"]["evidence_hits"] == 4
    assert report["aggregate"]["matched_headings"] == 1
    assert report["aggregate"]["field_recall"] == 1.0
    assert report["by_class"]["paper"]["candidate_precision"] == 1.0
    assert report["by_tag"]["seal_watermark"]["expected_fields"] == 1
    assert "Gold " not in json.dumps(report)
    assert "Synthetic " not in json.dumps(report)
    assert "not human-confirmed" in report["note"]


def test_suite_rejects_missing_class_and_tampered_source(tmp_path: Path) -> None:
    module = _module()
    suite = _suite(tmp_path)
    payload = json.loads(suite.read_text())
    payload["cases"].pop()
    suite.write_text(json.dumps(payload))
    with pytest.raises(module.EvaluationError, match="4-500 cases"):
        module.load_suite(suite)
    suite = _suite(tmp_path)
    (tmp_path / "paper.txt").write_text("changed")
    with pytest.raises(module.EvaluationError, match="Source SHA-256 mismatch"):
        module.load_suite(suite)


def test_suite_rejects_duplicate_business_document_and_boolean_field_page(tmp_path: Path) -> None:
    module = _module()
    suite = _suite(tmp_path)
    payload = json.loads(suite.read_text())
    payload["cases"][1]["document_id"] = payload["cases"][0]["document_id"]
    suite.write_text(json.dumps(payload))
    with pytest.raises(module.EvaluationError, match="distinct business document"):
        module.load_suite(suite)

    payload = json.loads(_suite(tmp_path).read_text())
    payload["cases"][1]["source"] = payload["cases"][0]["source"]
    payload["cases"][1]["sha256"] = payload["cases"][0]["sha256"]
    suite.write_text(json.dumps(payload))
    with pytest.raises(module.EvaluationError, match="distinct source file"):
        module.load_suite(suite)

    payload = json.loads(_suite(tmp_path).read_text())
    payload["cases"][0]["expected_fields"][0]["page_no"] = True
    suite.write_text(json.dumps(payload))
    with pytest.raises(module.EvaluationError, match="Invalid field page"):
        module.load_suite(suite)


def test_suite_rejects_symlinked_source_inside_sample_directory(tmp_path: Path) -> None:
    module = _module()
    suite = _suite(tmp_path)
    payload = json.loads(suite.read_text())
    (tmp_path / "source-link.txt").symlink_to(tmp_path / "paper.txt")
    payload["cases"][1]["source"] = "source-link.txt"
    suite.write_text(json.dumps(payload))
    with pytest.raises(module.EvaluationError, match="Source unavailable"):
        module.load_suite(suite)


def test_evaluation_rejects_wrong_business_identity_and_public_origin(tmp_path: Path) -> None:
    module = _module()
    case = module.load_suite(_suite(tmp_path))[0]

    def wrong(path: str) -> Any:
        payload = _get(path)
        if path.startswith("/extractions/"):
            payload["run"]["revision_id"] = "other-revision"
        return payload

    with pytest.raises(module.EvaluationError, match="identity or extraction state"):
        module.evaluate_case(case, wrong)
    with pytest.raises(module.EvaluationError, match="private or loopback"):
        module.BusinessReader("http://8.8.8.8:8088")


def test_matching_value_does_not_hide_wrong_evidence_page(tmp_path: Path) -> None:
    module = _module()
    case = module.load_suite(_suite(tmp_path))[1]
    case["expected_fields"][0]["page_no"] = 2
    report = module.evaluate_case(case, _get)
    assert report["matched_fields"] == 1
    assert report["evidence_hits"] == 0


def test_duplicate_value_uses_candidate_with_valid_frozen_evidence(tmp_path: Path) -> None:
    module = _module()
    case = module.load_suite(_suite(tmp_path))[1]

    def duplicate(path: str) -> Any:
        payload = _get(path)
        if path.startswith("/extractions/"):
            payload["candidates"].insert(0, {
                "field_code": "title", "value": "Gold paper", "evidence_id": "missing-evidence",
            })
        return payload

    result = module.evaluate_case(case, duplicate)
    assert result["matched_fields"] == 1
    assert result["candidate_fields"] == 2
    assert result["evidence_hits"] == 1


def test_duplicate_fields_maximize_distinct_evidence_matches(tmp_path: Path) -> None:
    module = _module()
    case = module.load_suite(_suite(tmp_path))[1]
    case["expected_fields"][0].pop("page_no")
    case["expected_fields"].append({"code": "title", "value": "Gold paper", "page_no": 1})

    def duplicate(path: str) -> Any:
        payload = _get(path)
        if path.startswith("/extractions/"):
            payload["candidates"].append({
                "field_code": "title", "value": "Gold paper", "evidence_id": "ev-paper-page2",
            })
        elif path.endswith("/evidence"):
            snippet = "Gold paper on page two"
            payload.append({
                "id": "ev-paper-page2", "revision_id": "rev-paper", "document_id": "doc-paper",
                "snippet": snippet, "snippet_sha256": hashlib.sha256(snippet.encode()).hexdigest(), "page_no": 2,
            })
        return payload

    result = module.evaluate_case(case, duplicate)
    assert result["matched_fields"] == 2
    assert result["matched_candidates"] == 2
    assert result["evidence_hits"] == 2


def test_evaluator_reads_real_business_api_contract(tmp_path: Path) -> None:
    module = _module()
    source = tmp_path / "notice.html"
    source.write_text("<h1>Synthetic notice</h1>", encoding="utf-8")
    store = BusinessStore(tmp_path / "business.sqlite3")
    store.initialize()
    upload = ImmutableUploadStore(tmp_path, max_bytes=1024).store(io.BytesIO(source.read_bytes()), filename=source.name)
    document = store.create_document(upload, original_name=source.name, template_code="official_document")
    parse = ParseInfo(
        id=7, sha256=upload.sha256, short_id=upload.sha256[:12], tier="flash", page_range="1",
        status="done", privacy="local", created_at=1, updated_at=2, done_at=2,
    )
    revision = store.add_completed_revision(document.id, parse=parse, producer_version="4.0.6")
    locator = f"doc:{revision.short_id}/tier:flash/page:1"
    doclib = Mock(spec=DoclibInterface)
    doclib.get_parse.return_value = parse
    doclib.get_doc.return_value.page_count = 1
    doclib.read_parse_content.return_value = DocContentResponse(
        sha256=upload.sha256, short_id=revision.short_id, tier="flash", content="标题：Synthetic notice",
        request_scope=ContentRequestScope(locator=locator),
    )
    writer = EvidenceWriter(store=store, doclib=doclib)
    extractor = FieldExtraction(store=store, doclib=doclib, evidence_writer=writer)
    extractor.enqueue(revision.id)
    run = extractor.process_next()
    assert run is not None and run.status == "done"
    app = create_app(
        workflow=Mock(), store=store, evidence_reader=EvidenceReader(store=store, doclib=doclib),
        evidence_writer=writer, field_extraction=extractor,
    )
    client = TestClient(app)

    def get(path: str) -> Any:
        response = client.get(f"/api/business{path}")
        assert response.status_code == 200, response.text
        return response.json()

    case = {
        "id": "synthetic-notice", "category": "official_document", "sha256": upload.sha256,
        "document_id": document.id, "revision_id": revision.id, "run_id": run.id,
        "expected_fields": [{"code": "title", "value": "Synthetic notice",
                             "evidence_quote": "标题：Synthetic notice", "page_no": 1}],
    }
    result = module.evaluate_case(case, get)
    assert result["matched_fields"] == 1 and result["evidence_hits"] == 1
    assert "Synthetic notice" not in json.dumps(result)


def test_evaluation_report_never_overwrites_prior_or_concurrent_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    suite_dir = tmp_path / "samples"
    suite_dir.mkdir()
    suite = _suite(suite_dir)
    output = tmp_path / "reports" / "metrics.json"
    monkeypatch.setattr(module, "BusinessReader", lambda _url: SimpleNamespace(get=_get))
    monkeypatch.setattr(sys, "argv", [
        "evaluate_business_samples.py", "--suite", str(suite), "--base-url", "http://127.0.0.1:8088",
        "--environment", "Mac synthetic contract", "--output", str(output),
    ])
    assert module.main() == 0
    first = output.read_bytes()
    assert module.main() == 1
    assert output.read_bytes() == first
    assert not list(output.parent.glob(".metrics.json.*"))

    output.unlink()

    def concurrent_writer(_source: Path, target: Path) -> None:
        target.write_bytes(b"another evaluator")
        raise FileExistsError(target)

    monkeypatch.setattr(module.os, "link", concurrent_writer)
    assert module.main() == 1
    assert output.read_bytes() == b"another evaluator"
    assert not list(output.parent.glob(".metrics.json.*"))
