"""Explicit, offline business API process bootstrap."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI

from ...doclib import DoclibClient
from ...version import __version__
from ..documents import DoclibGateway, ImmutableUploadStore
from ..services import (
    BusinessDiscovery, DocumentTaskWorker, DocumentWorkflow, EvidenceReader, EvidenceWriter,
    ExtractionWorker, FieldExtraction,
)
from ..store import BusinessStore
from .app import create_app


@dataclass(frozen=True)
class ServerConfig:
    database_path: Path
    upload_root: Path
    doclib_url: str
    max_upload_bytes: int

    @classmethod
    def from_environment(cls) -> ServerConfig:
        def required(name: str) -> str:
            value = os.environ.get(name, "").strip()
            if not value:
                raise ValueError(f"{name} is required")
            return value

        database_path = Path(required("MINERU_BUSINESS_DB_PATH"))
        upload_root = Path(required("MINERU_BUSINESS_UPLOAD_ROOT"))
        if not database_path.is_absolute() or not upload_root.is_absolute():
            raise ValueError("Business data paths must be absolute")
        doclib_url = required("MINERU_BUSINESS_DOCLIB_URL")
        parsed = urlsplit(doclib_url)
        if (
            parsed.scheme != "http"
            or not parsed.hostname
            or parsed.port is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("MINERU_BUSINESS_DOCLIB_URL must be a plain HTTP host and port")
        try:
            max_upload_bytes = int(required("MINERU_BUSINESS_MAX_UPLOAD_BYTES"))
        except ValueError as exc:
            raise ValueError("MINERU_BUSINESS_MAX_UPLOAD_BYTES must be a positive integer") from exc
        if max_upload_bytes < 1:
            raise ValueError("MINERU_BUSINESS_MAX_UPLOAD_BYTES must be a positive integer")
        return cls(database_path, upload_root, doclib_url.rstrip("/"), max_upload_bytes)


def build_app(config: ServerConfig) -> FastAPI:
    """Build one API process with no model or GPU dependency at runtime."""
    development_web_root = Path(__file__).resolve().parents[3] / "business-web" / "dist"
    web_root = Path(os.environ.get("MINERU_BUSINESS_WEB_ROOT", str(development_web_root)))
    if not web_root.is_absolute() or web_root.is_symlink():
        raise ValueError("MINERU_BUSINESS_WEB_ROOT must be an absolute, non-symlink directory")
    if os.environ.get("MINERU_BUSINESS_REQUIRE_WEB") == "1" and not (web_root / "index.html").is_file():
        raise ValueError("Business Web assets are required but were not built")
    store = BusinessStore(config.database_path)
    store.initialize()
    uploads = ImmutableUploadStore(config.upload_root, max_bytes=config.max_upload_bytes)
    doclib = DoclibClient(base_url=config.doclib_url)
    workflow = DocumentWorkflow(
        uploads=uploads,
        store=store,
        gateway=DoclibGateway(doclib, shared_root=config.upload_root),
        doclib=doclib,
        producer_version=__version__,
    )
    evidence_writer = EvidenceWriter(store=store, doclib=doclib)
    extraction_doclib = DoclibClient(base_url=config.doclib_url)
    extraction_writer = EvidenceWriter(store=store, doclib=extraction_doclib)
    extraction = FieldExtraction(store=store, doclib=extraction_doclib, evidence_writer=extraction_writer)
    recovery_doclib = DoclibClient(base_url=config.doclib_url, timeout=10)
    recovery_workflow = DocumentWorkflow(
        uploads=uploads,
        store=store,
        gateway=DoclibGateway(recovery_doclib, shared_root=config.upload_root),
        doclib=recovery_doclib,
        producer_version=__version__,
    )
    return create_app(
        workflow=workflow,
        store=store,
        evidence_reader=EvidenceReader(store=store, doclib=doclib),
        evidence_writer=evidence_writer,
        field_extraction=extraction,
        extraction_worker=ExtractionWorker(extraction),
        document_task_worker=DocumentTaskWorker(recovery_workflow, store),
        discovery=BusinessDiscovery(store=store, doclib=doclib),
        uploads=uploads,
        web_root=web_root if (web_root / "index.html").is_file() else None,
    )


def main() -> None:
    import uvicorn

    app = build_app(ServerConfig.from_environment())
    uvicorn.run(app, host="0.0.0.0", port=8080)


if __name__ == "__main__":
    main()


__all__ = ["ServerConfig", "build_app", "main"]
