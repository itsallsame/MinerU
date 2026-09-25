"""Small open HTTP surface; Doclib stays behind the business service."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from ...filetypes import (
    FLASH_ONLY_PARSE_EXTENSIONS,
    IMAGE_EXTENSIONS,
    MIME_TYPE_BY_EXTENSION,
    PARSEABLE_EXTENSIONS,
    TIERED_PARSE_EXTENSIONS,
)
from ...types import Tier
from ..documents import ImmutableUploadStore, UploadError, UploadIntegrityError
from ..domain import (
    AuditEvent,
    AuditPage,
    BusinessDocument,
    ConfirmedField,
    ConfirmedResult,
    EvidenceSnapshot,
    ExtractionRun,
    FieldCandidate,
    FieldDecision,
    FieldType,
    IngestTask,
    IssueResolution,
    ParseRevision,
    QualityIssue,
    ReviewSource,
    TemplateField,
    TemplateVersion,
    TaskStatus,
)
from ..services import (
    BusinessDiscovery,
    BusinessSearchPage,
    DiscoveryError,
    DocumentWorkflow,
    DocumentWorkflowError,
    DocumentTaskWorker,
    EvidenceCaptureError,
    EvidenceInspection,
    EvidenceReader,
    EvidenceWriter,
    ExtractionWorker,
    FieldExtraction,
    HistoricalRead,
    NavigationStatus,
    RevisionSearchPage,
    RevisionBlockSearchPage,
    RevisionDiffPage,
    RevisionOutlinePage,
    BusinessStructurePage,
    StructureBlock,
)
from ..store import (
    BusinessStore, BusinessStoreError, ExtractionRequestConflict, TaskRetryRequestConflict, TemplateRequestConflict,
    UploadRequestConflict,
)
from .body_limit import UploadBodyLimit


class FieldDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str
    evidence_id: str
    source: ReviewSource
    reason: str | None = None


class FieldDecisionView(BaseModel):
    id: str
    run_id: str
    field_code: str
    previous_value: str | None
    value: str
    evidence_id: str
    basis: str
    source: ReviewSource
    reason: str | None
    created_at_ms: int

    @classmethod
    def from_record(cls, decision: FieldDecision) -> FieldDecisionView:
        return cls(**vars(decision))


class IssueResolutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["resolved", "ignored"]
    source: ReviewSource
    reason: str


class IssueResolutionView(BaseModel):
    id: str
    issue_id: str
    run_id: str
    previous_status: str
    status: str
    source: ReviewSource
    reason: str
    created_at_ms: int

    @classmethod
    def from_record(cls, resolution: IssueResolution) -> IssueResolutionView:
        return cls(**vars(resolution))


class ConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: ReviewSource


class ConfirmedFieldView(BaseModel):
    field_code: str
    value: str
    evidence_id: str
    decision_id: str
    basis: str

    @classmethod
    def from_record(cls, field: ConfirmedField) -> ConfirmedFieldView:
        return cls(**vars(field))


class ConfirmedResultView(BaseModel):
    id: str
    run_id: str
    version: int
    revision_id: str
    template_code: str
    template_version: int
    fields: tuple[ConfirmedFieldView, ...]
    fields_sha256: str
    source: ReviewSource
    created_at_ms: int

    @classmethod
    def from_record(cls, result: ConfirmedResult) -> ConfirmedResultView:
        return cls(
            id=result.id, run_id=result.run_id, version=result.version, revision_id=result.revision_id,
            template_code=result.template_code, template_version=result.template_version,
            fields=tuple(ConfirmedFieldView.from_record(field) for field in result.fields),
            fields_sha256=result.fields_sha256, source=result.source, created_at_ms=result.created_at_ms,
        )


class AuditEventView(BaseModel):
    id: str
    run_id: str
    action: str
    target_id: str
    source: ReviewSource
    old_value: str | None
    new_value: str
    reason: str | None
    created_at_ms: int

    @classmethod
    def from_record(cls, event: AuditEvent) -> AuditEventView:
        return cls(**vars(event))


class AuditRecordView(BaseModel):
    event: AuditEventView
    document_id: str
    document_name: str
    revision_id: str


class AuditPageView(BaseModel):
    items: list[AuditRecordView]
    next_before: str | None

    @classmethod
    def from_page(cls, page: AuditPage) -> AuditPageView:
        return cls(items=[AuditRecordView(
            event=AuditEventView.from_record(record.event), document_id=record.document_id,
            document_name=record.document_name, revision_id=record.revision_id,
        ) for record in page.items], next_before=page.next_before)


class ExtractionRunView(BaseModel):
    id: str
    revision_id: str
    template_code: str
    template_version: int
    status: str
    error_code: str | None
    created_at_ms: int
    updated_at_ms: int

    @classmethod
    def from_record(cls, run: ExtractionRun) -> ExtractionRunView:
        return cls(
            id=run.id, revision_id=run.revision_id, template_code=run.template_code,
            template_version=run.template_version, status=run.status, error_code=run.error_code,
            created_at_ms=run.created_at_ms, updated_at_ms=run.updated_at_ms,
        )


class FieldCandidateView(BaseModel):
    id: str
    run_id: str
    field_code: str
    value: str
    evidence_id: str
    method: str
    created_at_ms: int

    @classmethod
    def from_record(cls, candidate: FieldCandidate) -> FieldCandidateView:
        return cls(**vars(candidate))


class QualityIssueView(BaseModel):
    id: str
    run_id: str
    field_code: str | None
    code: str
    severity: str
    status: str
    created_at_ms: int

    @classmethod
    def from_record(cls, issue: QualityIssue) -> QualityIssueView:
        return cls(**vars(issue))


class QualityStatsView(BaseModel):
    documents: int
    parse_pending: int
    parse_failed: int
    parse_done: int
    revisions: int
    extraction_pending: int
    extraction_failed: int
    extraction_done: int
    open_issues: int
    confirmed_runs: int
    result_versions: int


class ExtractionResultView(BaseModel):
    run: ExtractionRunView
    candidates: list[FieldCandidateView]
    issues: list[QualityIssueView]


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
    template_code: str | None
    template_version: int | None

    @classmethod
    def from_record(cls, document: BusinessDocument) -> DocumentView:
        return cls(
            id=document.id,
            original_name=document.original_name,
            sha256=document.sha256,
            size=document.size,
            created_at_ms=document.created_at_ms,
            template_code=document.template_code,
            template_version=document.template_version,
        )


class TaskView(BaseModel):
    id: str
    document_id: str
    requested_tier: Tier | None
    actual_tier: Tier | None
    status: str
    error_code: str | None
    cancel_effect: str | None
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
            cancel_effect=task.cancel_effect,
            created_at_ms=task.created_at_ms,
            updated_at_ms=task.updated_at_ms,
        )


class SubmissionView(BaseModel):
    document: DocumentView
    task: TaskView


class DocumentListItemView(BaseModel):
    document: DocumentView
    task: TaskView | None


class DocumentListView(BaseModel):
    items: list[DocumentListItemView]
    total: int
    limit: int
    offset: int


class CapabilitiesView(BaseModel):
    max_upload_bytes: int
    tiered_extensions: tuple[str, ...]
    flash_only_extensions: tuple[str, ...]
    parseable_extensions: tuple[str, ...]
    tiers: tuple[Tier, ...]


class RevisionView(BaseModel):
    id: str
    document_id: str
    short_id: str
    tier: Tier
    page_range: str
    producer_version: str
    model_ref: str | None
    created_at_ms: int

    @classmethod
    def from_record(cls, revision: ParseRevision) -> RevisionView:
        return cls(
            id=revision.id,
            document_id=revision.document_id,
            short_id=revision.short_id,
            tier=revision.tier,
            page_range=revision.page_range,
            producer_version=revision.producer_version,
            model_ref=revision.model_ref,
            created_at_ms=revision.created_at_ms,
        )


class BusinessSearchHitView(BaseModel):
    document: DocumentView
    revision_id: str
    tier: Tier
    snippet: str
    state: Literal["current_index_unconfirmed"] = "current_index_unconfirmed"


class BusinessSearchView(BaseModel):
    items: list[BusinessSearchHitView]
    scan_complete: bool
    scanned_doclib_hits: int

    @classmethod
    def from_page(cls, page: BusinessSearchPage) -> BusinessSearchView:
        return cls(
            items=[BusinessSearchHitView(
                document=DocumentView.from_record(hit.document), revision_id=hit.revision_id,
                tier=hit.tier, snippet=hit.snippet,
            ) for hit in page.items],
            scan_complete=page.scan_complete, scanned_doclib_hits=page.scanned_doclib_hits,
        )


class HistoricalReadView(BaseModel):
    document_id: str
    revision_id: str
    locator: str
    tier: Tier
    content: str
    truncated: bool
    next_locator: str | None
    state: Literal["historical_parse_unconfirmed"] = "historical_parse_unconfirmed"

    @classmethod
    def from_read(cls, read: HistoricalRead) -> HistoricalReadView:
        return cls(**vars(read))


class RevisionDiffItemView(BaseModel):
    page_no: int
    status: Literal["same", "changed", "only_left", "only_right"]
    left_locator: str | None
    right_locator: str | None
    state: Literal["historical_parse_unconfirmed"] = "historical_parse_unconfirmed"


class RevisionDiffPageView(BaseModel):
    left_revision_id: str
    right_revision_id: str
    items: list[RevisionDiffItemView]
    next_page: int | None
    scanned_pages: int

    @classmethod
    def from_page(cls, page: RevisionDiffPage) -> RevisionDiffPageView:
        return cls(
            left_revision_id=page.left_revision_id, right_revision_id=page.right_revision_id,
            items=[RevisionDiffItemView(**vars(item)) for item in page.items],
            next_page=page.next_page, scanned_pages=page.scanned_pages,
        )


class RevisionSearchHitView(BaseModel):
    locator: str
    page_no: int
    snippet: str
    state: Literal["historical_parse_unconfirmed"] = "historical_parse_unconfirmed"


class RevisionSearchView(BaseModel):
    revision_id: str
    items: list[RevisionSearchHitView]
    next_page: int | None
    scanned_pages: int

    @classmethod
    def from_page(cls, page: RevisionSearchPage) -> RevisionSearchView:
        return cls(
            revision_id=page.revision_id,
            items=[RevisionSearchHitView(**vars(hit)) for hit in page.items],
            next_page=page.next_page, scanned_pages=page.scanned_pages,
        )


class RevisionBlockHitView(BaseModel):
    locator: str
    page_no: int
    block_no: int
    snippet: str
    bbox: tuple[float, float, float, float] | None
    state: Literal["historical_parse_unconfirmed"] = "historical_parse_unconfirmed"


class RevisionBlockSearchView(BaseModel):
    revision_id: str
    items: list[RevisionBlockHitView]
    next_page: int | None
    scanned_pages: int

    @classmethod
    def from_page(cls, page: RevisionBlockSearchPage) -> RevisionBlockSearchView:
        return cls(
            revision_id=page.revision_id,
            items=[RevisionBlockHitView(**vars(hit)) for hit in page.items],
            next_page=page.next_page, scanned_pages=page.scanned_pages,
        )


class RevisionHeadingView(BaseModel):
    level: int
    title: str
    page_no: int
    locator: str
    state: Literal["historical_parse_unconfirmed"] = "historical_parse_unconfirmed"


class RevisionOutlineView(BaseModel):
    revision_id: str
    items: list[RevisionHeadingView]
    next_page: int | None
    scanned_pages: int

    @classmethod
    def from_page(cls, page: RevisionOutlinePage) -> RevisionOutlineView:
        return cls(
            revision_id=page.revision_id,
            items=[RevisionHeadingView(**vars(item)) for item in page.items],
            next_page=page.next_page, scanned_pages=page.scanned_pages,
        )


class StructureBlockView(BaseModel):
    type: str
    block_no: int | None
    locator: str
    path: tuple[int, ...]
    preview: str
    level: int | None
    bbox: tuple[float, float, float, float] | None
    children: list[StructureBlockView]
    state: Literal["historical_parse_unconfirmed"] = "historical_parse_unconfirmed"

    @classmethod
    def from_block(cls, block: StructureBlock) -> StructureBlockView:
        return cls(
            type=block.type, block_no=block.block_no, locator=block.locator, path=block.path,
            preview=block.preview, level=block.level, bbox=block.bbox,
            children=[cls.from_block(child) for child in block.children],
        )


class BusinessStructureView(BaseModel):
    document_id: str
    revision_id: str
    page_no: int
    blocks: list[StructureBlockView]

    @classmethod
    def from_page(cls, page: BusinessStructurePage) -> BusinessStructureView:
        return cls(
            document_id=page.document_id, revision_id=page.revision_id, page_no=page.page_no,
            blocks=[StructureBlockView.from_block(block) for block in page.blocks],
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
    *, workflow: DocumentWorkflow, store: BusinessStore, evidence_reader: EvidenceReader,
    evidence_writer: EvidenceWriter, field_extraction: FieldExtraction | None = None,
    extraction_worker: ExtractionWorker | None = None, discovery: BusinessDiscovery | None = None,
    document_task_worker: DocumentTaskWorker | None = None,
    uploads: ImmutableUploadStore | None = None,
    web_root: Path | None = None,
) -> FastAPI:
    """Build the shared open API; network placement is a deployment boundary."""
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        document_started = False
        extraction_started = False
        try:
            if document_task_worker is not None:
                document_task_worker.start()
                document_started = True
            if extraction_worker is not None:
                extraction_worker.start()
                extraction_started = True
            yield
        finally:
            if extraction_started and extraction_worker is not None:
                extraction_worker.stop()
            if document_started and document_task_worker is not None:
                document_task_worker.stop()

    app = FastAPI(title="MinerU Business Documents", version="0.1.0", lifespan=lifespan)
    if uploads is not None:
        app.add_middleware(UploadBodyLimit, max_upload_bytes=uploads.max_bytes)

    @app.get("/api/business/quality-stats", response_model=QualityStatsView)
    def get_quality_stats() -> QualityStatsView:
        return QualityStatsView(**store.quality_stats())

    @app.get("/api/business/capabilities", response_model=CapabilitiesView)
    def get_capabilities() -> CapabilitiesView:
        if uploads is None:
            raise HTTPException(status_code=503, detail="Upload store is not configured")
        return CapabilitiesView(
            max_upload_bytes=uploads.max_bytes,
            tiered_extensions=tuple(sorted(TIERED_PARSE_EXTENSIONS)),
            flash_only_extensions=tuple(sorted(FLASH_ONLY_PARSE_EXTENSIONS)),
            parseable_extensions=tuple(sorted(PARSEABLE_EXTENSIONS)),
            tiers=("flash", "basic", "standard", "advanced"),
        )

    @app.get("/api/business/search", response_model=BusinessSearchView)
    def search_business_documents(
        query: Annotated[str, Query(min_length=1, max_length=200)],
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
    ) -> BusinessSearchView:
        if discovery is None:
            raise HTTPException(status_code=503, detail="Business discovery is not configured")
        try:
            return BusinessSearchView.from_page(discovery.search(query, limit=limit))
        except DiscoveryError as exc:
            status_code = 422 if exc.code == "invalid_search_request" else 503
            raise HTTPException(status_code=status_code, detail=exc.code) from exc

    @app.get("/api/business/revisions/{revision_id}/content", response_model=HistoricalReadView)
    def read_revision_content(
        revision_id: str,
        locator: str,
        limit: Annotated[int, Query(ge=1, le=30000)] = 12000,
    ) -> HistoricalReadView:
        if discovery is None:
            raise HTTPException(status_code=503, detail="Business discovery is not configured")
        try:
            return HistoricalReadView.from_read(discovery.read(revision_id, locator, limit=limit))
        except DiscoveryError as exc:
            status_code = (
                404 if exc.code == "revision_not_found" else
                422 if exc.code.startswith("invalid_") else
                503 if exc.code == "doclib_unavailable" else 409
            )
            raise HTTPException(status_code=status_code, detail=exc.code) from exc

    @app.get("/api/business/revisions/{revision_id}/search", response_model=RevisionSearchView)
    def search_revision_content(
        revision_id: str,
        query: Annotated[str, Query(min_length=1, max_length=200)],
        start_page: Annotated[int | None, Query(ge=1)] = None,
    ) -> RevisionSearchView:
        if discovery is None:
            raise HTTPException(status_code=503, detail="Business discovery is not configured")
        try:
            return RevisionSearchView.from_page(discovery.search_revision(revision_id, query, start_page=start_page))
        except DiscoveryError as exc:
            status_code = (
                404 if exc.code == "revision_not_found" else
                422 if exc.code.startswith("invalid_") else
                503 if exc.code == "doclib_unavailable" else 409
            )
            raise HTTPException(status_code=status_code, detail=exc.code) from exc

    @app.get("/api/business/revisions/{revision_id}/diff", response_model=RevisionDiffPageView)
    def diff_revisions(
        revision_id: str, other_revision_id: str,
        start_page: Annotated[int | None, Query(ge=1)] = None,
    ) -> RevisionDiffPageView:
        if discovery is None:
            raise HTTPException(status_code=503, detail="Business discovery is not configured")
        try:
            return RevisionDiffPageView.from_page(
                discovery.diff_revisions(revision_id, other_revision_id, start_page=start_page)
            )
        except DiscoveryError as exc:
            status_code = (
                404 if exc.code == "revision_not_found" else
                422 if exc.code.startswith("invalid_") else
                503 if exc.code == "doclib_unavailable" else 409
            )
            raise HTTPException(status_code=status_code, detail=exc.code) from exc

    @app.get("/api/business/revisions/{revision_id}/search-blocks", response_model=RevisionBlockSearchView)
    def search_revision_blocks(
        revision_id: str,
        query: Annotated[str, Query(min_length=1, max_length=200)],
        start_page: Annotated[int | None, Query(ge=1)] = None,
    ) -> RevisionBlockSearchView:
        if discovery is None:
            raise HTTPException(status_code=503, detail="Business discovery is not configured")
        try:
            return RevisionBlockSearchView.from_page(discovery.search_blocks(revision_id, query, start_page=start_page))
        except DiscoveryError as exc:
            status_code = (
                404 if exc.code == "revision_not_found" else
                422 if exc.code.startswith("invalid_") else
                503 if exc.code == "doclib_unavailable" else 409
            )
            raise HTTPException(status_code=status_code, detail=exc.code) from exc

    @app.get("/api/business/revisions/{revision_id}/outline", response_model=RevisionOutlineView)
    def get_revision_outline(
        revision_id: str, start_page: Annotated[int | None, Query(ge=1)] = None,
    ) -> RevisionOutlineView:
        if discovery is None:
            raise HTTPException(status_code=503, detail="Business discovery is not configured")
        try:
            return RevisionOutlineView.from_page(discovery.outline(revision_id, start_page=start_page))
        except DiscoveryError as exc:
            status_code = (
                404 if exc.code == "revision_not_found" else
                422 if exc.code.startswith("invalid_") else
                503 if exc.code == "doclib_unavailable" else 409
            )
            raise HTTPException(status_code=status_code, detail=exc.code) from exc

    @app.get("/api/business/revisions/{revision_id}/structure", response_model=BusinessStructureView)
    def get_revision_structure(
        revision_id: str, page_no: Annotated[int, Query(ge=1)],
    ) -> BusinessStructureView:
        if discovery is None:
            raise HTTPException(status_code=503, detail="Business discovery is not configured")
        try:
            return BusinessStructureView.from_page(discovery.structure(revision_id, page_no))
        except DiscoveryError as exc:
            status_code = (
                404 if exc.code == "revision_not_found" else
                422 if exc.code.startswith("invalid_") else
                503 if exc.code == "doclib_unavailable" else 409
            )
            raise HTTPException(status_code=status_code, detail=exc.code) from exc

    @app.post(
        "/api/business/extractions/{run_id}/fields/{field_code}/decisions",
        response_model=FieldDecisionView, status_code=201,
    )
    def decide_field(run_id: str, field_code: str, request: FieldDecisionRequest) -> FieldDecisionView:
        try:
            decision = store.decide_field(
                run_id, field_code=field_code, value=request.value, evidence_id=request.evidence_id,
                source=request.source, reason=request.reason,
            )
        except BusinessStoreError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return FieldDecisionView.from_record(decision)

    @app.get("/api/business/extractions/{run_id}/decisions", response_model=list[FieldDecisionView])
    def list_field_decisions(run_id: str) -> list[FieldDecisionView]:
        if store.get_extraction(run_id) is None:
            raise HTTPException(status_code=404, detail="Extraction not found")
        return [FieldDecisionView.from_record(item) for item in store.list_field_decisions(run_id)]

    @app.post("/api/business/issues/{issue_id}/resolutions", response_model=IssueResolutionView, status_code=201)
    def resolve_issue(issue_id: str, request: IssueResolutionRequest) -> IssueResolutionView:
        try:
            resolution = store.resolve_issue(
                issue_id, status=request.status, source=request.source, reason=request.reason
            )
        except BusinessStoreError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return IssueResolutionView.from_record(resolution)

    @app.get("/api/business/extractions/{run_id}/resolutions", response_model=list[IssueResolutionView])
    def list_issue_resolutions(run_id: str) -> list[IssueResolutionView]:
        if store.get_extraction(run_id) is None:
            raise HTTPException(status_code=404, detail="Extraction not found")
        return [IssueResolutionView.from_record(item) for item in store.list_issue_resolutions(run_id)]

    @app.post("/api/business/extractions/{run_id}/confirm", response_model=ConfirmedResultView, status_code=201)
    def confirm_result(run_id: str, request: ConfirmationRequest) -> ConfirmedResultView:
        try:
            result = store.confirm_result(run_id, source=request.source)
        except BusinessStoreError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return ConfirmedResultView.from_record(result)

    @app.get("/api/business/extractions/{run_id}/results", response_model=list[ConfirmedResultView])
    def list_confirmed_results(run_id: str) -> list[ConfirmedResultView]:
        if store.get_extraction(run_id) is None:
            raise HTTPException(status_code=404, detail="Extraction not found")
        try:
            return [ConfirmedResultView.from_record(item) for item in store.list_confirmed_results(run_id)]
        except BusinessStoreError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/business/results/{result_id}", response_model=ConfirmedResultView)
    def get_confirmed_result(result_id: str) -> ConfirmedResultView:
        try:
            result = store.get_confirmed_result(result_id)
        except BusinessStoreError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(status_code=404, detail="Result not found")
        return ConfirmedResultView.from_record(result)

    @app.get("/api/business/extractions/{run_id}/audit", response_model=list[AuditEventView])
    def list_audit_events(run_id: str) -> list[AuditEventView]:
        if store.get_extraction(run_id) is None:
            raise HTTPException(status_code=404, detail="Extraction not found")
        return [AuditEventView.from_record(item) for item in store.list_audit_events(run_id)]

    @app.get("/api/business/audit", response_model=AuditPageView)
    def audit_page(limit: Annotated[int, Query(ge=1, le=100)] = 20, before: str | None = None) -> AuditPageView:
        try:
            return AuditPageView.from_page(store.audit_page(limit=limit, before=before))
        except BusinessStoreError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/business/revisions/{revision_id}/extractions", response_model=ExtractionRunView, status_code=202)
    def extract_fields(
        revision_id: str,
        request_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> ExtractionRunView:
        if field_extraction is None:
            raise HTTPException(status_code=503, detail="Field extraction is not configured")
        try:
            run = field_extraction.enqueue(revision_id, request_key=request_key)
        except ExtractionRequestConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except BusinessStoreError as exc:
            status_code = 404 if str(exc) == "Parse revision not found" else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        return ExtractionRunView.from_record(run)

    @app.get("/api/business/extraction-requests/{request_key}", response_model=ExtractionRunView)
    def get_extraction_request(request_key: str) -> ExtractionRunView:
        try:
            run = store.get_extraction_request(request_key)
        except BusinessStoreError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if run is None:
            raise HTTPException(status_code=404, detail="Extraction request not recorded at lookup")
        return ExtractionRunView.from_record(run)

    @app.get("/api/business/revisions/{revision_id}/extractions", response_model=list[ExtractionRunView])
    def list_extractions(revision_id: str) -> list[ExtractionRunView]:
        if store.get_revision(revision_id) is None:
            raise HTTPException(status_code=404, detail="Revision not found")
        return [ExtractionRunView.from_record(run) for run in store.list_extractions(revision_id)]

    @app.get("/api/business/extractions/{run_id}", response_model=ExtractionResultView)
    def get_extraction(run_id: str) -> ExtractionResultView:
        run = store.get_extraction(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Extraction not found")
        return ExtractionResultView(
            run=ExtractionRunView.from_record(run),
            candidates=[FieldCandidateView.from_record(item) for item in store.list_field_candidates(run_id)]
            if run.status == "done" else [],
            issues=[QualityIssueView.from_record(item) for item in store.list_quality_issues(run_id)]
            if run.status == "done" else [],
        )

    @app.get("/api/business/templates", response_model=list[TemplateView])
    def list_templates() -> list[TemplateView]:
        return [TemplateView.from_record(template) for template in store.list_templates()]

    @app.post("/api/business/templates", response_model=TemplateView, status_code=201)
    def create_template(
        request: TemplateCreateRequest,
        request_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> TemplateView:
        try:
            template = store.create_template(
                code=request.code, name=request.name, fields=request.domain_fields(), request_key=request_key,
            )
        except TemplateRequestConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except BusinessStoreError as exc:
            status_code = 422 if str(exc) == "Invalid template idempotency key" else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return TemplateView.from_record(template)

    @app.get("/api/business/templates/{code}", response_model=TemplateView)
    def get_template(code: str, version: int | None = None) -> TemplateView:
        template = store.get_template(code, version=version)
        if template is None:
            raise HTTPException(status_code=404, detail="Template version not found")
        return TemplateView.from_record(template)

    @app.get("/api/business/template-requests/{request_key}", response_model=TemplateView)
    def get_template_request(request_key: str) -> TemplateView:
        try:
            template = store.get_template_request(request_key)
        except BusinessStoreError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if template is None:
            raise HTTPException(status_code=404, detail="Template request not found")
        return TemplateView.from_record(template)

    @app.put("/api/business/templates/{code}", response_model=TemplateView)
    def update_template(
        code: str, request: TemplateWriteRequest,
        request_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> TemplateView:
        try:
            template = store.update_template(
                code, name=request.name, fields=request.domain_fields(), request_key=request_key,
            )
        except TemplateRequestConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except BusinessStoreError as exc:
            status_code = 422 if str(exc) == "Invalid template idempotency key" else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return TemplateView.from_record(template)

    @app.post("/api/business/templates/{code}/disable", response_model=TemplateView)
    def disable_template(
        code: str, request_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> TemplateView:
        try:
            template = store.disable_template(code, request_key=request_key)
        except TemplateRequestConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except BusinessStoreError as exc:
            status_code = 422 if str(exc) == "Invalid template idempotency key" else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        return TemplateView.from_record(template)

    @app.post("/api/business/documents", response_model=SubmissionView, status_code=202)
    def submit_document(
        file: Annotated[UploadFile, File()], tier: Annotated[Tier | None, Form()] = None,
        template_code: Annotated[str | None, Form()] = None,
        request_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> SubmissionView:
        if not file.filename:
            raise HTTPException(status_code=422, detail="File name is required")
        try:
            result = workflow.submit(
                file.file, filename=file.filename, tier=tier, template_code=template_code, request_key=request_key,
            )
        except UploadError as exc:
            status_code = 413 if "exceeds" in str(exc) else 422
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        except UploadRequestConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except DocumentWorkflowError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return SubmissionView(document=DocumentView.from_record(result.document), task=TaskView.from_record(result.task))

    @app.get("/api/business/upload-requests/{request_key}", response_model=SubmissionView)
    def get_upload_request(request_key: str) -> SubmissionView:
        try:
            result = store.get_upload_request(request_key)
        except BusinessStoreError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(status_code=404, detail="Upload request not found")
        document, task = result
        return SubmissionView(document=DocumentView.from_record(document), task=TaskView.from_record(task))

    @app.get("/api/business/documents", response_model=DocumentListView)
    def list_documents(
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
        template_code: str | None = None,
        status: TaskStatus | None = None,
    ) -> DocumentListView:
        items, total = store.list_documents(limit=limit, offset=offset, template_code=template_code, status=status)
        return DocumentListView(
            items=[DocumentListItemView(
                document=DocumentView.from_record(document),
                task=TaskView.from_record(task) if task is not None else None,
            ) for document, task in items],
            total=total, limit=limit, offset=offset,
        )

    @app.get("/api/business/documents/{document_id}", response_model=DocumentView)
    def get_document(document_id: str) -> DocumentView:
        document = store.get_document(document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Document not found")
        return DocumentView.from_record(document)

    @app.head("/api/business/documents/{document_id}/source", response_class=FileResponse)
    @app.get("/api/business/documents/{document_id}/source", response_class=FileResponse)
    def get_document_source(document_id: str) -> FileResponse:
        document = store.get_document(document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Document not found")
        if uploads is None:
            raise HTTPException(status_code=503, detail="Upload store is not configured")
        try:
            path = uploads.verified_source_path(
                document.storage_key, sha256=document.sha256, size=document.size,
            )
        except UploadIntegrityError as exc:
            raise HTTPException(status_code=409, detail="Document source no longer matches its recorded identity") from exc
        except UploadError as exc:
            raise HTTPException(status_code=409, detail="Document source is unavailable") from exc
        extension = Path(document.storage_key).suffix.removeprefix(".")
        disposition = "inline" if extension == "pdf" or extension in IMAGE_EXTENSIONS else "attachment"
        return FileResponse(
            path, media_type=MIME_TYPE_BY_EXTENSION.get(extension, "application/octet-stream"),
            filename=document.original_name, content_disposition_type=disposition,
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"},
        )

    @app.get("/api/business/documents/{document_id}/revisions", response_model=list[RevisionView])
    def list_revisions(document_id: str) -> list[RevisionView]:
        if store.get_document(document_id) is None:
            raise HTTPException(status_code=404, detail="Document not found")
        return [RevisionView.from_record(revision) for revision in store.list_revisions(document_id)]

    @app.get("/api/business/revisions/{revision_id}/evidence", response_model=list[EvidenceView])
    def list_evidence(revision_id: str) -> list[EvidenceView]:
        if store.get_revision(revision_id) is None:
            raise HTTPException(status_code=404, detail="Revision not found")
        try:
            return [EvidenceView.from_record(evidence) for evidence in store.list_evidence(revision_id)]
        except BusinessStoreError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

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
        try:
            inspection = evidence_reader.inspect(evidence_id)
        except BusinessStoreError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
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
    def retry_task(
        task_id: str,
        request_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> TaskView:
        try:
            task = workflow.retry(task_id, request_key=request_key)
        except TaskRetryRequestConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except BusinessStoreError as exc:
            status_code = 404 if str(exc) == "Task not found" else 422
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        except DocumentWorkflowError as exc:
            status_code = 404 if str(exc) == "Task not found" else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        except UploadError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return TaskView.from_record(task)

    @app.get("/api/business/task-retry-requests/{request_key}", response_model=TaskView)
    def get_task_retry_request(request_key: str) -> TaskView:
        try:
            task = store.get_task_retry_request(request_key)
        except BusinessStoreError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if task is None:
            raise HTTPException(status_code=404, detail="Task retry request not recorded at lookup")
        return TaskView.from_record(task)

    @app.post("/api/business/tasks/{task_id}/cancel", response_model=TaskView)
    def cancel_task(task_id: str) -> TaskView:
        try:
            task = workflow.cancel(task_id)
        except DocumentWorkflowError as exc:
            status_code = 404 if str(exc) == "Task not found" else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        return TaskView.from_record(task)

    if web_root is not None:
        app.mount("/", StaticFiles(directory=web_root, html=True), name="business-web")

    return app


__all__ = [
    "AuditEventView",
    "BusinessSearchHitView",
    "BusinessSearchView",
    "ConfirmationRequest",
    "ConfirmedFieldView",
    "ConfirmedResultView",
    "CapabilitiesView",
    "DocumentListItemView",
    "DocumentListView",
    "DocumentView",
    "EvidenceCaptureRequest",
    "EvidenceInspectionView",
    "EvidenceView",
    "ExtractionResultView",
    "ExtractionRunView",
    "FieldCandidateView",
    "FieldDecisionRequest",
    "FieldDecisionView",
    "HistoricalReadView",
    "IssueResolutionRequest",
    "IssueResolutionView",
    "QualityIssueView",
    "RevisionView",
    "RevisionSearchHitView",
    "RevisionSearchView",
    "RevisionHeadingView",
    "RevisionOutlineView",
    "BusinessStructureView",
    "StructureBlockView",
    "SubmissionView",
    "TaskView",
    "TemplateCreateRequest",
    "TemplateFieldView",
    "TemplateView",
    "TemplateWriteRequest",
    "create_app",
]
