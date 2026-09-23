"""Production bootstrap and deployment boundary checks without model weights."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from mineru.business.api import create_app
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
    assert "COPY business-web/dist/" in dockerfile
    assert "WEB_ASSET_MANIFEST_SHA256" in dockerfile
    assert "--no-index" in dockerfile
    assert "COPY models/" not in dockerfile
    assert "MINERU_MODELS_HOST_DIR" not in api_service
    assert "gpus:" not in api_service
    assert "/srv/mineru-inbox:rw" in api_service
    assert "/srv/mineru-inbox:ro" in worker_service
    assert "MINERU_BUSINESS_BIND_ADDRESS:-127.0.0.1" in api_service
    assert "MINERU_BUSINESS_WEB_ROOT: /opt/mineru/business-web/dist" in api_service
    assert "ports:" not in worker_service


def test_business_web_is_served_same_origin_without_shadowing_api(tmp_path: Path) -> None:
    web_root = tmp_path / "web"
    web_root.mkdir()
    (web_root / "index.html").write_text("<title>Business Web</title>")
    (web_root / "app.js").write_text("export const ready = true;")
    app = create_app(
        workflow=Mock(), store=Mock(), evidence_reader=Mock(), evidence_writer=Mock(), web_root=web_root,
    )
    client = TestClient(app)
    assert "Business Web" in client.get("/").text
    assert "ready = true" in client.get("/app.js").text
    assert client.get("/api/business/documents?limit=0").status_code == 422


def test_bootstrap_uses_explicit_production_web_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    database_dir = tmp_path / "business"
    database_dir.mkdir()
    web_root = tmp_path / "built-web"
    web_root.mkdir()
    (web_root / "index.html").write_text("<title>Packaged Web</title>")
    config = ServerConfig(database_dir / "business.sqlite3", upload_root, "http://127.0.0.1:15980", 1024)
    monkeypatch.setenv("MINERU_BUSINESS_WEB_ROOT", str(web_root))
    monkeypatch.setenv("MINERU_BUSINESS_REQUIRE_WEB", "1")
    client = TestClient(build_app(config))
    assert "Packaged Web" in client.get("/").text
    monkeypatch.setenv("MINERU_BUSINESS_WEB_ROOT", str(tmp_path / "missing"))
    with pytest.raises(ValueError, match="assets are required"):
        build_app(config)


__all__ = []
