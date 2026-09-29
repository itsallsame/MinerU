"""Versioned business structure edits never mutate Doclib or frozen review state."""

from __future__ import annotations

import io
import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient
from httpx import Response

from mineru.business.api import create_app
from mineru.business.documents import ImmutableUploadStore
from mineru.business.services import BusinessDiscovery
from mineru.business.store import BusinessStore, BusinessStoreError
from mineru.doclib import DoclibInterface, ParseInfo
from mineru.doclib.types import ParseBlockSummary, ParseStructureNodeTextResponse, ParseStructureResponse


def _editor_api(tmp_path: Path) -> tuple[TestClient, BusinessStore, Mock, str, str]:
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    source = ImmutableUploadStore(upload_root, max_bytes=1024)
    stored = source.store(io.BytesIO(b"<h1>Structure test</h1>"), filename="test.html")
    store = BusinessStore(tmp_path / "business.sqlite3")
    store.initialize()
    document = store.create_document(stored, original_name="test.html")
    parse = ParseInfo(
        id=7, sha256=stored.sha256, short_id=stored.sha256[:12], tier="flash", page_range="1",
        status="done", privacy="local", created_at=1, updated_at=2, done_at=2,
    )
    revision = store.add_completed_revision(document.id, parse=parse, producer_version="4.0.6")
    doclib = Mock(spec=DoclibInterface)
    prefix = f"doc:{parse.short_id}/tier:flash/page:1"
    doclib.read_parse_structure.return_value = ParseStructureResponse(
        sha256=stored.sha256, short_id=parse.short_id, tier="flash", page_no=1,
        blocks=[
            ParseBlockSummary(type="text", block_no=1, locator=f"{prefix}/block:1", path=[0], preview="Alpha text"),
            ParseBlockSummary(type="text", block_no=2, locator=f"{prefix}/block:2", path=[1], preview="Beta text"),
            ParseBlockSummary(
                type="list", block_no=3, locator=f"{prefix}/block:3", path=[2],
                children=[ParseBlockSummary(
                    type="text", block_no=3, locator=f"{prefix}/block:3", path=[2, 0], preview="Nested text",
                )],
            ),
        ],
    )

    def node_text(parse_id: int, page_no: int, path: str) -> ParseStructureNodeTextResponse:
        assert parse_id == 7 and page_no == 1
        texts = {"0": ("Alpha text", 1), "1": ("Beta text", 2), "2.0": ("Nested text", 3)}
        value, block_no = texts[path]
        return ParseStructureNodeTextResponse(
            sha256=stored.sha256, short_id=parse.short_id, tier="flash", page_no=1,
            path=[int(part) for part in path.split(".")], type="text", block_no=block_no,
            locator=f"{prefix}/block:{block_no}", editable=True, text=value,
        )

    doclib.read_parse_structure_node_text.side_effect = node_text
    api = TestClient(create_app(
        workflow=Mock(), store=store, discovery=BusinessDiscovery(store=store, doclib=doclib),
        evidence_reader=Mock(), evidence_writer=Mock(),
    ))
    return api, store, doclib, document.id, revision.id


def _edit(
    api: TestClient, revision_id: str, version: int, operation: dict, *,
    reason: str = "Manual review", request_key: str | None = None,
) -> Response:
    return api.post(
        f"/api/business/revisions/{revision_id}/structure/operations",
        json={"page_no": 1, "expected_version": version, "operation": operation, "source": "api", "reason": reason},
        headers={"Idempotency-Key": request_key} if request_key is not None else None,
    )


def test_structure_operations_append_versions_and_preserve_native_parse(tmp_path: Path) -> None:
    api, store, doclib, document_id, revision_id = _editor_api(tmp_path)
    route = f"/api/business/revisions/{revision_id}/structure/derived"
    initial = api.get(route, params={"page_no": 1})
    assert initial.status_code == 200
    start = initial.json()
    assert start["version"] == 0 and start["state"] == "historical_parse_unconfirmed"
    assert start["document_id"] == document_id
    first_id, second_id, list_id = [node["node_id"] for node in start["nodes"]]
    assert start["nodes"][0]["text_override"] is None

    type_op = {
        "op": "set_type", "node_id": first_id, "type": "paragraph_title", "level": 2,
    }
    changed_type = _edit(api, revision_id, 0, type_op, request_key="structure-type-001")
    assert changed_type.status_code == 201, changed_type.text
    assert changed_type.json()["version"] == 1
    assert changed_type.json()["nodes"][0]["type"] == "paragraph_title"
    assert _edit(api, revision_id, 0, {"op": "delete", "node_id": second_id}).status_code == 409
    replay = _edit(api, revision_id, 0, type_op, request_key="structure-type-001")
    assert replay.status_code == 201 and replay.json() == changed_type.json()
    receipt = api.get("/api/business/structure-requests/structure-type-001")
    assert receipt.status_code == 200 and receipt.json() == changed_type.json()
    assert _edit(api, revision_id, 0, {"op": "delete", "node_id": second_id},
                 request_key="structure-type-001").status_code == 409

    changed_text = _edit(api, revision_id, 1, {
        "op": "set_text", "node_id": first_id, "text": "Corrected Alpha text",
    })
    assert changed_text.status_code == 201, changed_text.text
    assert changed_text.json()["nodes"][0]["text_override"] == "Corrected Alpha text"
    assert changed_text.json()["nodes"][0]["origins"][0]["path"] == [0]
    assert changed_text.json()["nodes"][0]["content_changed"]

    split = _edit(api, revision_id, 2, {"op": "split", "node_id": first_id, "offset": 10})
    assert split.status_code == 201, split.text
    left, right = split.json()["nodes"][:2]
    assert left["node_id"] != first_id and right["node_id"] != first_id
    assert left["origins"] == right["origins"]
    assert left["text_override"] + right["text_override"] == "Corrected Alpha text"

    merged = _edit(api, revision_id, 3, {
        "op": "merge", "first_id": left["node_id"], "second_id": right["node_id"], "separator": "",
    })
    assert merged.status_code == 201, merged.text
    merged_id = merged.json()["nodes"][0]["node_id"]
    assert merged.json()["nodes"][0]["text_override"] == "Corrected Alpha text"
    assert len(merged.json()["nodes"][0]["origins"]) == 1

    moved = _edit(api, revision_id, 4, {
        "op": "move", "node_id": merged_id, "parent_id": list_id, "index": 1,
    })
    assert moved.status_code == 201, moved.text
    assert moved.json()["nodes"][1]["node_id"] == list_id
    assert moved.json()["nodes"][1]["children"][1]["node_id"] == merged_id
    assert _edit(api, revision_id, 5, {
        "op": "move", "node_id": list_id, "parent_id": merged_id, "index": 0,
    }).status_code == 422

    inserted = _edit(api, revision_id, 5, {
        "op": "insert", "parent_id": list_id, "index": 2, "type": "text", "text": "Human-only note",
    })
    assert inserted.status_code == 201, inserted.text
    manual = inserted.json()["nodes"][1]["children"][2]
    assert manual["origins"] == [] and manual["text_override"] == "Human-only note"
    removed = _edit(api, revision_id, 6, {"op": "delete", "node_id": manual["node_id"]})
    assert removed.status_code == 201 and removed.json()["version"] == 7
    assert all(child["node_id"] != manual["node_id"] for child in removed.json()["nodes"][1]["children"])

    prior = api.get(route, params={"page_no": 1, "version": 1}).json()
    assert prior["nodes"][0]["node_id"] == first_id
    assert prior["nodes"][0]["text_override"] is None
    assert api.get(route, params={"page_no": 1, "version": 0}).json()["nodes"][0]["type"] == "text"
    assert api.get(route, params={"page_no": 1}).json()["version"] == 7
    assert _edit(api, revision_id, 0, type_op, request_key="structure-type-001").json()["version"] == 1
    assert api.get(route, params={"page_no": 1}).json()["version"] == 7
    assert doclib.read_parse_structure.return_value.blocks[0].type == "text"
    assert store.get_structure_version(revision_id, 1, 3).version == 3


def test_structure_operations_reject_invalid_input_and_cross_revision_state(tmp_path: Path) -> None:
    api, store, _doclib, document_id, revision_id = _editor_api(tmp_path)
    initial = api.get(f"/api/business/revisions/{revision_id}/structure/derived", params={"page_no": 1}).json()
    first_id = initial["nodes"][0]["node_id"]
    assert _edit(api, revision_id, 0, {"op": "set_text", "node_id": first_id, "text": " "}).status_code == 422
    assert _edit(api, revision_id, 0, {"op": "split", "node_id": first_id, "offset": 0}).status_code == 422
    assert _edit(api, revision_id, 0, {"op": "merge", "first_id": first_id, "second_id": "absent"}).status_code == 422
    assert _edit(api, revision_id, 0, {"op": "set_type", "node_id": first_id, "type": "paragraph_title"}).status_code == 422
    assert _edit(api, revision_id, 0, {"op": "set_text", "node_id": first_id, "text": "x", "forged": True}).status_code == 422
    assert _edit(api, revision_id, 0, {"op": "delete", "node_id": first_id},
                 request_key="short").status_code == 422
    assert api.get(f"/api/business/revisions/{revision_id}/structure/derived", params={"page_no": 2}).status_code == 422
    assert store.get_structure_version(revision_id, 1) is None

    first_revision = store.get_revision(revision_id)
    next_parse = ParseInfo(
        id=8, sha256=first_revision.sha256, short_id=first_revision.short_id, tier="flash", page_range="1",
        status="done", privacy="local", created_at=3, updated_at=4, done_at=4,
    )
    other = store.add_completed_revision(document_id, parse=next_parse, producer_version="4.0.7")
    written = _edit(api, revision_id, 0, {"op": "delete", "node_id": first_id})
    assert written.status_code == 201
    other_page = api.get(f"/api/business/revisions/{other.id}/structure/derived", params={"page_no": 1}).json()
    assert other_page["version"] == 0 and len(other_page["nodes"]) == 3
    assert other_page["nodes"][0]["node_id"] != first_id
    assert _edit(api, other.id, 0, {"op": "delete", "node_id": first_id}).status_code == 422

    with closing(sqlite3.connect(tmp_path / "business.sqlite3")) as database, database:
        database.execute(
            "UPDATE structure_versions SET nodes_json='[]' WHERE revision_id=? AND page_no=1 AND version=1",
            (revision_id,),
        )
    try:
        store.get_structure_version(revision_id, 1)
    except BusinessStoreError as exc:
        assert "checksum" in str(exc)
    else:
        raise AssertionError("Corrupt structure version was accepted")
    assert api.get(f"/api/business/revisions/{revision_id}/structure/derived", params={"page_no": 1}).status_code == 409
