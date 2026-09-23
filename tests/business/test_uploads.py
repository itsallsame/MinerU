"""The worker must never observe a partial or unbounded business upload."""

from __future__ import annotations

import hashlib
import io
import stat
import uuid
from pathlib import Path

import pytest

from mineru.business.documents import ImmutableUploadStore, UploadError
from mineru.business.documents import uploads


def test_upload_is_published_with_opaque_name_and_content_identity(tmp_path: Path) -> None:
    root = tmp_path / "shared"
    root.mkdir()
    store = ImmutableUploadStore(root, max_bytes=100)
    payload = b"%PDF-1.7\ncomplete document"

    first = store.store(io.BytesIO(payload), filename="../../contract.pdf")
    second = store.store(io.BytesIO(payload), filename="C:\\other\\contract.pdf")

    assert first.sha256 == second.sha256 == hashlib.sha256(payload).hexdigest()
    assert first.path != second.path
    assert first.path.parent == root
    assert first.path.name not in {"contract.pdf", "../../contract.pdf"}
    assert first.path.read_bytes() == payload
    assert first.size == len(payload)
    assert first.extension == "pdf"
    assert stat.S_IMODE(first.path.stat().st_mode) == 0o444
    assert not list(root.glob("*.part"))


def test_rejected_upload_never_publishes_partial_file(tmp_path: Path) -> None:
    root = tmp_path / "shared"
    root.mkdir()
    store = ImmutableUploadStore(root, max_bytes=4)

    with pytest.raises(UploadError, match="exceeds"):
        store.store(io.BytesIO(b"12345"), filename="report.html")
    with pytest.raises(UploadError, match="Empty"):
        store.store(io.BytesIO(b""), filename="report.html")
    with pytest.raises(UploadError, match="Unsupported"):
        store.store(io.BytesIO(b"data"), filename="report.exe")
    assert list(root.iterdir()) == []


def test_stream_failure_cleans_staging_file(tmp_path: Path) -> None:
    root = tmp_path / "shared"
    root.mkdir()

    class InterruptedStream:
        def __init__(self) -> None:
            self.calls = 0

        def read(self, _size: int) -> bytes:
            self.calls += 1
            if self.calls == 1:
                assert not any(path.suffix == ".html" for path in root.iterdir())
                return b"partial"
            raise OSError("upload disconnected")

    with pytest.raises(OSError, match="disconnected"):
        ImmutableUploadStore(root, max_bytes=100).store(InterruptedStream(), filename="report.html")
    assert list(root.iterdir()) == []


def test_upload_root_must_be_preexisting_real_directory(tmp_path: Path) -> None:
    root = tmp_path / "shared"
    with pytest.raises(FileNotFoundError):
        ImmutableUploadStore(root, max_bytes=1)
    root.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(root)
    with pytest.raises(UploadError, match="symbolic link"):
        ImmutableUploadStore(alias, max_bytes=1)


def test_generated_name_collision_never_removes_existing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "shared"
    root.mkdir()
    token = uuid.UUID(int=42)
    monkeypatch.setattr(uploads.uuid, "uuid4", lambda: token)
    existing = root / f"{token.hex}.html"
    existing.write_bytes(b"original")

    with pytest.raises(FileExistsError):
        ImmutableUploadStore(root, max_bytes=100).store(io.BytesIO(b"new"), filename="report.html")
    assert existing.read_bytes() == b"original"
    assert sorted(root.iterdir()) == [existing]


def test_only_matching_unregistered_upload_can_be_rolled_back(tmp_path: Path) -> None:
    root = tmp_path / "shared"
    root.mkdir()
    store = ImmutableUploadStore(root, max_bytes=100)
    uploaded = store.store(io.BytesIO(b"<h1>Accepted</h1>"), filename="report.html")
    uploaded.path.chmod(0o600)
    uploaded.path.write_bytes(b"changed")
    with pytest.raises(UploadError, match="changed"):
        store.discard_unregistered(uploaded)
    assert uploaded.path.exists()
    uploaded.path.write_bytes(b"<h1>Accepted</h1>")
    store.discard_unregistered(uploaded)
    assert not uploaded.path.exists()
