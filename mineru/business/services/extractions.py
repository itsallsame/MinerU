"""Conservative label rules over a selected historical MinerU parse batch."""

from __future__ import annotations

import re

from ...doclib import DoclibInterface
from ...doclib.locators import page_ref
from ...errors import MineruError
from ...parser.page_range import parse_page_range_set
from ..domain import ExtractionRun, TemplateField
from ..store import BusinessStore, BusinessStoreError
from .evidence import EvidenceCaptureError, EvidenceWriter

_MAX_PAGES = 1000
_PAGE_LIMIT = 30000


class FieldExtraction:
    """Make unconfirmed proposals; never infer absent values or claim calibrated confidence."""

    def __init__(self, *, store: BusinessStore, doclib: DoclibInterface, evidence_writer: EvidenceWriter) -> None:
        self._store = store
        self._doclib = doclib
        self._evidence_writer = evidence_writer

    def enqueue(self, revision_id: str) -> ExtractionRun:
        return self._store.enqueue_extraction(revision_id)

    def process_next(self) -> ExtractionRun | None:
        run = self._store.claim_next_extraction()
        if run is None:
            return None
        return self._process_claimed(run)

    def _fail(self, run: ExtractionRun, error_code: str) -> ExtractionRun:
        assert run.claim_token is not None
        try:
            return self._store.fail_extraction(run.id, claim_token=run.claim_token, error_code=error_code)
        except BusinessStoreError:
            latest = self._store.get_extraction(run.id)
            assert latest is not None
            return latest  # A newer worker may own this run after lease expiry.

    def _process_claimed(self, run: ExtractionRun) -> ExtractionRun:
        assert run.claim_token is not None
        revision_id = run.revision_id
        revision = self._store.get_revision(revision_id)
        template = self._store.get_template(run.template_code, version=run.template_version)
        assert revision is not None and template is not None
        try:
            self._store.renew_extraction_lease(run.id, claim_token=run.claim_token)
            for batch in revision.parse_batches:
                parse = self._doclib.get_parse(batch.parse_id)
                if (
                    parse.status not in ("done", "superseded")
                    or parse.sha256 != revision.sha256
                    or parse.short_id != revision.short_id
                    or parse.tier != revision.tier
                    or parse.page_range != batch.page_range
                ):
                    return self._fail(run, "historical_parse_mismatch")
            page_numbers = sorted(parse_page_range_set(revision.page_range))
            if not page_numbers or len(page_numbers) > _MAX_PAGES:
                return self._fail(run, "page_range_unsupported")
            doc = self._doclib.get_doc(revision.sha256)
            self._store.renew_extraction_lease(run.id, claim_token=run.claim_token)
            page_count = doc.page_count
            complete_coverage = isinstance(page_count, int) and page_count > 0 and page_numbers == list(
                range(1, page_count + 1)
            )
            for page_no in page_numbers:
                self._store.renew_extraction_lease(run.id, claim_token=run.claim_token)
                locator = page_ref(revision.short_id, revision.tier, page_no)
                parse_id = revision.parse_id_for_page(page_no)
                assert parse_id is not None
                page = self._doclib.read_parse_content(parse_id, locator, limit=_PAGE_LIMIT)
                if (
                    page.truncated
                    or page.sha256 != revision.sha256
                    or page.short_id != revision.short_id
                    or page.tier != revision.tier
                    or page.request_scope.locator != locator
                ):
                    return self._fail(run, "historical_content_incomplete")
                matches = [
                    (field.code, value)
                    for field in template.fields
                    for value in _label_values(page.content, field)
                ]
                if not matches:
                    continue
                self._store.renew_extraction_lease(run.id, claim_token=run.claim_token)
                evidence = self._evidence_writer.capture(revision_id, locator=locator)
                for field_code, value in matches:
                    self._store.add_field_candidate(
                        run.id, claim_token=run.claim_token, field_code=field_code,
                        value=value, evidence_id=evidence.id,
                    )
            return self._store.finish_extraction(
                run.id, claim_token=run.claim_token, complete_coverage=complete_coverage
            )
        except BusinessStoreError:
            return self._fail(run, "candidate_integrity_failed")
        except (MineruError, EvidenceCaptureError, ValueError):
            return self._fail(run, "historical_content_unavailable")


def _label_values(content: str, field: TemplateField) -> tuple[str, ...]:
    """Only explicit label-value lines qualify; headings and prose are not guessed."""
    label = re.escape(field.label)
    pattern = re.compile(
        rf"^[ \t]*(?:[-*][ \t]*)?(?:\*\*)?{label}(?:\*\*)?[ \t]*[:：][ \t]*(.+?)[ \t]*$",
        re.MULTILINE,
    )
    result: list[str] = []
    for match in pattern.finditer(content):
        value = match.group(1).strip().strip("*").strip()
        if 0 < len(value) <= 2000 and value not in result:
            result.append(value)
    return tuple(result)


__all__ = ["FieldExtraction"]
