"""Small open HTTP surface; Doclib stays behind the business service."""

from __future__ import annotations

from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict

from ...types import Tier
from ..documents import UploadError
from ..domain import BusinessDocument, EvidenceSnapshot, FieldType, IngestTask, ParseRevision, TemplateField, TemplateVersion
from ..services import (
    DocumentWorkflow,
    DocumentWorkflowError,
    EvidenceCaptureError,
    EvidenceInspection,
    EvidenceReader,
    EvidenceWriter,
    NavigationStatus,
)
from ..store import BusinessStore, BusinessStoreError


class TemplateFieldView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    label: str
    type: FieldType = "text"
    required: bool = False


class TemplateWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    fields: list[TemplateFieldView]

    def domain_fields(self) -> tuple[TemplateField, ...]:
        return tuple(TemplateField(**field.model_dump()) for field in self.fields)


class TemplateCreateRequest(TemplateWriteRequest):
    code: str


class TemplateView(BaseModel):
    code: str
    name: str
    version: int
    built_in: bool
    enabled: bool
    fields: tuple[TemplateFieldView, ...]
    created_at_ms: int

    @classmethod
    def from_record(cls, template: TemplateVersion) -> TemplateView:
        return cls(
            code=template.code, name=template.name, version=template.version, built_in=template.built_in,
            enabled=template.enabled, fields=tuple(TemplateFieldView(**vars(field)) for field in template.fields),
            created_at_ms=template.created_at_ms,
        )


class DocumentView(BaseModel):
    id: str
    original_name: str
    sha256: str
    size: int
    created_at_ms: int

    @classmethod
    def from_record(cls, document: BusinessDocument) -> DocumentView:
        return cls(
            id=document.id,
            original_name=document.original_name,
            sha256=document.sha256,
            size=document.size,
            created_at_ms=document.created_at_ms,
        )


class TaskView(BaseModel):
    id: str
    document_id: str
    requested_tier: Tier | None
    actual_tier: Tier | None
    status: str
    error_code: str | None
    created_at_ms: int
    updated_at_ms: int

    @classmethod
    def from_record(cls, task: IngestTask) -> TaskView:
        return cls(
            id=task.id,
            document_id=task.document_id,
            requested_tier=task.requested_tier,
            actual_tier=task.actual_tier,
            status=task.status,
            error_code=task.error_code,
            created_at_ms=task.created_at_ms,
            updated_at_ms=task.updated_at_ms,
        )


class SubmissionView(BaseModel):
    document: DocumentView
    task: TaskView


class RevisionView(BaseModel):
    id: str
    document_id: str
    tier: Tier
    producer_version: str
    model_ref: str | None
    created_at_ms: int

    @classmethod
    def from_record(cls, revision: ParseRevision) -> RevisionView:
        return cls(
            id=revision.id,
            document_id=revision.document_id,
            tier=revision.tier,
            producer_version=revision.producer_version,
            model_ref=revision.model_ref,
            created_at_ms=revision.created_at_ms,
        )


class EvidenceView(BaseModel):
    id: str
    revision_id: str
    document_id: str
    locator: str
    page_no: int
    block_no: int | None
    bbox: tuple[float, float, float, float] | None
    snippet: str
    snippet_sha256: str
    created_at_ms: int

    @classmethod
    def from_record(cls, evidence: EvidenceSnapshot) -> EvidenceView:
        return cls(**vars(evidence))


class EvidenceInspectionView(EvidenceView):
    navigation_status: NavigationStatus

    @classmethod
    def from_inspection(cls, inspection: EvidenceInspection) -> EvidenceInspectionView:
        return cls(**vars(inspection.snapshot), navigation_status=inspection.navigation_status)


class EvidenceCaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locator: str


def create_app(
    *, workflow: DocumentWorkflow, store: BusinessStore, evidence_reader: EvidenceReader, evidence_writer: EvidenceWriter
) -> FastAPI:
    """Build the shared open API; network placement is a deployment boundary."""
    app = FastAPI(title="MinerU Business Documents", version="0.1.0")

    @app.get("/api/business/templates", response_model=list[TemplateView])
    def list_templates() -> list[TemplateView]:
        return [TemplateView.from_record(template) for template in store.list_templates()]

    @app.post("/api/business/templates", response_model=TemplateView, status_code=201)
    def create_template(request: TemplateCreateRequest) -> TemplateView:
        try:
            template = store.create_template(code=request.code, name=request.name, fields=request.domain_fields())
        except BusinessStoreError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return TemplateView.from_record(template)

    @app.get("/api/business/templates/{code}", response_model=TemplateView)
    def get_template(code: str, version: int | None = None) -> TemplateView:
        template = store.get_template(code, version=version)
        if template is None:
            raise HTTPException(status_code=404, detail="Template version not found")
        return TemplateView.from_record(template)

    @app.put("/api/business/templates/{code}", response_model=TemplateView)
    def update_template(code: str, request: TemplateWriteRequest) -> TemplateView:
        try:
            template = store.update_template(code, name=request.name, fields=request.domain_fields())
        except BusinessStoreError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return TemplateView.from_record(template)

    @app.post("/api/business/templates/{code}/disable", response_model=TemplateView)
    def disable_template(code: str) -> TemplateView:
        try:
            template = store.disable_template(code)
        except BusinessStoreError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return TemplateView.from_record(template)

    @app.post("/api/business/documents", response_model=SubmissionView, status_code=202)
    def submit_document(file: Annotated[UploadFile, File()], tier: Annotated[Tier | None, Form()] = None) -> SubmissionView:
        if not file.filename:
            raise HTTPException(status_code=422, detail="File name is required")
        try:
            result = workflow.submit(file.file, filename=file.filename, tier=tier)
        except UploadError as exc:
            status_code = 413 if "exceeds" in str(exc) else 422
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except DocumentWorkflowError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return SubmissionView(document=DocumentView.from_record(result.document), task=TaskView.from_record(result.task))

    @app.get("/api/business/documents/{document_id}", response_model=DocumentView)
    def get_document(document_id: str) -> DocumentView:
        document = store.get_document(document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Document not found")
        return DocumentView.from_record(document)

    @app.get("/api/business/documents/{document_id}/revisions", response_model=list[RevisionView])
    def list_revisions(document_id: str) -> list[RevisionView]:
        if store.get_document(document_id) is None:
            raise HTTPException(status_code=404, detail="Document not found")
        return [RevisionView.from_record(revision) for revision in store.list_revisions(document_id)]

    @app.get("/api/business/revisions/{revision_id}/evidence", response_model=list[EvidenceView])
    def list_evidence(revision_id: str) -> list[EvidenceView]:
        if store.get_revision(revision_id) is None:
            raise HTTPException(status_code=404, detail="Revision not found")
        return [EvidenceView.from_record(evidence) for evidence in store.list_evidence(revision_id)]

    @app.post("/api/business/revisions/{revision_id}/evidence", response_model=EvidenceView, status_code=201)
    def capture_evidence(revision_id: str, request: EvidenceCaptureRequest) -> EvidenceView:
        try:
            evidence = evidence_writer.capture(revision_id, locator=request.locator)
        except EvidenceCaptureError as exc:
            status_code = 404 if exc.code == "revision_not_found" else 422 if exc.code == "invalid_evidence_locator" else 409
            raise HTTPException(status_code=status_code, detail=exc.code) from exc
        return EvidenceView.from_record(evidence)

    @app.get("/api/business/evidence/{evidence_id}", response_model=EvidenceInspectionView)
    def inspect_evidence(evidence_id: str) -> EvidenceInspectionView:
        inspection = evidence_reader.inspect(evidence_id)
        if inspection is None:
            raise HTTPException(status_code=404, detail="Evidence not found")
        return EvidenceInspectionView.from_inspection(inspection)

    @app.get("/api/business/tasks/{task_id}", response_model=TaskView)
    def get_task(task_id: str) -> TaskView:
        try:
            task = workflow.refresh(task_id)
        except DocumentWorkflowError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return TaskView.from_record(task)

    @app.post("/api/business/tasks/{task_id}/retry", response_model=TaskView)
    def retry_task(task_id: str) -> TaskView:
        try:
            task = workflow.retry(task_id)
        except DocumentWorkflowError as exc:
            status_code = 404 if str(exc) == "Task not found" else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        except UploadError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return TaskView.from_record(task)

    return app


__all__ = [
    "DocumentView",
    "EvidenceCaptureRequest",
    "EvidenceInspectionView",
    "EvidenceView",
    "RevisionView",
    "SubmissionView",
    "TaskView",
    "TemplateCreateRequest",
    "TemplateFieldView",
    "TemplateView",
    "TemplateWriteRequest",
    "create_app",
]
