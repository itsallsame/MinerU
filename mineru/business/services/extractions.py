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

    def run(self, revision_id: str) -> ExtractionRun:
        run = self._store.create_extraction(revision_id)
        revision = self._store.get_revision(revision_id)
        template = self._store.get_template(run.template_code, version=run.template_version)
        assert revision is not None and template is not None
        try:
            parse = self._doclib.get_parse(revision.doclib_parse_id)
            if (
                parse.status not in ("done", "superseded")
                or parse.sha256 != revision.sha256
                or parse.short_id != revision.short_id
                or parse.tier != revision.tier
            ):
                return self._store.fail_extraction(run.id, error_code="historical_parse_mismatch")
            page_numbers = sorted(parse_page_range_set(parse.page_range))
            if not page_numbers or len(page_numbers) > _MAX_PAGES:
                return self._store.fail_extraction(run.id, error_code="page_range_unsupported")
            doc = self._doclib.get_doc(revision.sha256)
            page_count = doc.page_count
            complete_coverage = isinstance(page_count, int) and page_count > 0 and page_numbers == list(
                range(1, page_count + 1)
            )
            for page_no in page_numbers:
                locator = page_ref(revision.short_id, revision.tier, page_no)
                page = self._doclib.read_parse_content(revision.doclib_parse_id, locator, limit=_PAGE_LIMIT)
                if (
                    page.truncated
                    or page.sha256 != revision.sha256
                    or page.short_id != revision.short_id
                    or page.tier != revision.tier
                    or page.request_scope.locator != locator
                ):
                    return self._store.fail_extraction(run.id, error_code="historical_content_incomplete")
                matches = [
                    (field.code, value)
                    for field in template.fields
                    for value in _label_values(page.content, field)
                ]
                if not matches:
                    continue
                evidence = self._evidence_writer.capture(revision_id, locator=locator)
                for field_code, value in matches:
                    self._store.add_field_candidate(
                        run.id, field_code=field_code, value=value, evidence_id=evidence.id
                    )
            return self._store.finish_extraction(run.id, complete_coverage=complete_coverage)
        except BusinessStoreError:
            return self._store.fail_extraction(run.id, error_code="candidate_integrity_failed")
        except (MineruError, EvidenceCaptureError, ValueError):
            return self._store.fail_extraction(run.id, error_code="historical_content_unavailable")


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
