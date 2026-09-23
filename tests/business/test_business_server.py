"""Production bootstrap and deployment boundary checks without model weights."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mineru.business.api.server import ServerConfig, build_app


def test_server_config_requires_explicit_local_paths_and_http_endpoint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for key in (
        "MINERU_BUSINESS_DB_PATH",
        "MINERU_BUSINESS_UPLOAD_ROOT",
        "MINERU_BUSINESS_DOCLIB_URL",
        "MINERU_BUSINESS_MAX_UPLOAD_BYTES",
    ):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValueError, match="MINERU_BUSINESS_DB_PATH"):
        ServerConfig.from_environment()

    monkeypatch.setenv("MINERU_BUSINESS_DB_PATH", str(tmp_path / "business.sqlite3"))
    monkeypatch.setenv("MINERU_BUSINESS_UPLOAD_ROOT", str(tmp_path / "uploads"))
    monkeypatch.setenv("MINERU_BUSINESS_DOCLIB_URL", "http://doclib-worker:15980")
    monkeypatch.setenv("MINERU_BUSINESS_MAX_UPLOAD_BYTES", "1024")
    config = ServerConfig.from_environment()
    assert config.max_upload_bytes == 1024
    assert config.doclib_url == "http://doclib-worker:15980"

    for invalid in ("https://example.test:15980", "http://host:15980/path", "http://user@host:15980"):
        monkeypatch.setenv("MINERU_BUSINESS_DOCLIB_URL", invalid)
        with pytest.raises(ValueError, match="plain HTTP"):
            ServerConfig.from_environment()
    monkeypatch.setenv("MINERU_BUSINESS_DOCLIB_URL", "http://doclib-worker:15980")
    monkeypatch.setenv("MINERU_BUSINESS_MAX_UPLOAD_BYTES", "0")
    with pytest.raises(ValueError, match="positive integer"):
        ServerConfig.from_environment()


def test_server_bootstrap_initializes_open_api_without_contacting_doclib(tmp_path: Path) -> None:
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    database_dir = tmp_path / "business"
    database_dir.mkdir()
    config = ServerConfig(database_dir / "business.sqlite3", upload_root, "http://127.0.0.1:15980", 1024)
    app = build_app(config)
    client = TestClient(app)
    assert client.get("/api/business/documents/missing").status_code == 404
    assert config.database_path.is_file()
    assert "model" not in app.openapi()["info"]["title"].lower()


def test_business_image_and_compose_keep_models_out_of_api() -> None:
    root = Path(__file__).resolve().parents[2]
    dockerfile = (root / "docker/business-api/Dockerfile").read_text()
    compose = (root / "docker/compose.business.yaml").read_text()
    api_service = compose.split("  business-api:", 1)[1].split("  doclib-worker:", 1)[0]
    worker_service = compose.split("  doclib-worker:", 1)[1].split("networks:", 1)[0]
    assert "COPY mineru/" in dockerfile
    assert "--no-index" in dockerfile
    assert "COPY models/" not in dockerfile
    assert "MINERU_MODELS_HOST_DIR" not in api_service
    assert "gpus:" not in api_service
    assert "/srv/mineru-inbox:rw" in api_service
    assert "/srv/mineru-inbox:ro" in worker_service
    assert "MINERU_BUSINESS_BIND_ADDRESS:-127.0.0.1" in api_service
    assert "ports:" not in worker_service


__all__ = []
