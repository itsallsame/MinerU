"""Open upload ingress must reject large bodies before multipart spooling."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient

from mineru.business.api import create_app
from mineru.business.api.body_limit import MULTIPART_OVERHEAD_BYTES
from mineru.business.documents import ImmutableUploadStore


def _client(tmp_path: Path) -> tuple[TestClient, Mock]:
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    workflow = Mock()
    app = create_app(
        workflow=workflow,
        store=Mock(),
        evidence_reader=Mock(),
        evidence_writer=Mock(),
        uploads=ImmutableUploadStore(upload_root, max_bytes=1024),
    )
    return TestClient(app), workflow


def test_content_length_rejects_oversize_before_multipart_parse(tmp_path: Path) -> None:
    client, workflow = _client(tmp_path)
    response = client.post(
        "/api/business/documents",
        files={"file": ("oversize.txt", b"x" * (MULTIPART_OVERHEAD_BYTES + 1025), "text/plain")},
    )
    assert response.status_code == 413
    workflow.submit.assert_not_called()
    assert not list((tmp_path / "uploads").iterdir())


def test_unknown_length_stream_rejects_oversize_during_receive(tmp_path: Path) -> None:
    client, workflow = _client(tmp_path)
    boundary = "mineru-boundary"

    def chunks() -> Iterator[bytes]:
        yield f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="report.txt"\r\n\r\n'.encode()
        yield b"x" * (MULTIPART_OVERHEAD_BYTES + 1025)
        yield f"\r\n--{boundary}--\r\n".encode()

    response = client.post(
        "/api/business/documents",
        content=chunks(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    assert response.status_code == 413
    workflow.submit.assert_not_called()
    assert not list((tmp_path / "uploads").iterdir())


def test_declared_short_length_cannot_bypass_stream_limit(tmp_path: Path) -> None:
    client, workflow = _client(tmp_path)
    response = client.post(
        "/api/business/documents",
        content=b"x" * (MULTIPART_OVERHEAD_BYTES + 1025),
        headers={"Content-Type": "multipart/form-data; boundary=unused", "Content-Length": "1"},
    )
    assert response.status_code == 413
    workflow.submit.assert_not_called()


def test_invalid_content_length_is_rejected_before_parsing(tmp_path: Path) -> None:
    client, workflow = _client(tmp_path)
    response = client.post(
        "/api/business/documents",
        content=b"irrelevant",
        headers={"Content-Type": "multipart/form-data; boundary=unused", "Content-Length": "not-a-number"},
    )
    assert response.status_code == 400
    workflow.submit.assert_not_called()
