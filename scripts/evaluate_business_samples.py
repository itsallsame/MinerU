"""Read-only evaluation of four annotated business document classes against the open business API."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

CLASSES = frozenset({"official_document", "paper", "research_report", "newspaper"})
TAGS = frozenset({"handwritten", "cross_page_table", "seal_watermark"})
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
MAX_RESPONSE_BYTES = 10 * 1024 * 1024


class EvaluationError(RuntimeError):
    """Safe evaluation failure; never include annotated values or source content."""


def _required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvaluationError(f"{label} must be a non-empty string")
    return value


def _source_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_suite(path: Path) -> list[dict[str, Any]]:
    """Validate an external annotation suite; source files remain outside Git and reports."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EvaluationError("Cannot read annotation suite") from exc
    if not isinstance(payload, dict) or payload.get("schema") != 1 or not isinstance(payload.get("cases"), list):
        raise EvaluationError("Unsupported annotation suite schema")
    cases = payload["cases"]
    if len(cases) < 4 or len(cases) > 500:
        raise EvaluationError("Suite must contain 4-500 cases")
    seen_ids: set[str] = set()
    classes: set[str] = set()
    root = path.parent.resolve()
    for case in cases:
        if not isinstance(case, dict):
            raise EvaluationError("Case must be an object")
        case_id = _required_string(case.get("id"), "case.id")
        if case_id in seen_ids:
            raise EvaluationError("Case IDs must be unique")
        seen_ids.add(case_id)
        category = case.get("category")
        if not isinstance(category, str) or category not in CLASSES:
            raise EvaluationError(f"Unsupported category in case {case_id}")
        classes.add(category)
        for key in ("document_id", "revision_id", "run_id"):
            _required_string(case.get(key), f"{case_id}.{key}")
        expected_sha = case.get("sha256")
        if not isinstance(expected_sha, str) or SHA256_RE.fullmatch(expected_sha) is None:
            raise EvaluationError(f"Invalid SHA-256 in case {case_id}")
        relative = Path(_required_string(case.get("source"), f"{case_id}.source"))
        if relative.is_absolute() or ".." in relative.parts:
            raise EvaluationError(f"Source must be relative to the suite in case {case_id}")
        source = (root / relative).resolve()
        if not source.is_relative_to(root) or not source.is_file() or source.is_symlink():
            raise EvaluationError(f"Source unavailable in case {case_id}")
        if _source_sha256(source) != expected_sha:
            raise EvaluationError(f"Source SHA-256 mismatch in case {case_id}")
        fields = case.get("expected_fields")
        if not isinstance(fields, list) or not fields:
            raise EvaluationError(f"Expected fields missing in case {case_id}")
        for field in fields:
            if not isinstance(field, dict):
                raise EvaluationError(f"Invalid field annotation in case {case_id}")
            _required_string(field.get("code"), f"{case_id}.field.code")
            _required_string(field.get("value"), f"{case_id}.field.value")
            if "evidence_quote" in field:
                _required_string(field["evidence_quote"], f"{case_id}.field.evidence_quote")
            if "page_no" in field and (not isinstance(field["page_no"], int) or field["page_no"] < 1):
                raise EvaluationError(f"Invalid field page in case {case_id}")
        headings = case.get("expected_headings")
        if headings is not None:
            if not isinstance(headings, list):
                raise EvaluationError(f"Invalid heading annotations in case {case_id}")
            for heading in headings:
                if not isinstance(heading, dict):
                    raise EvaluationError(f"Invalid heading annotation in case {case_id}")
                _required_string(heading.get("title"), f"{case_id}.heading.title")
                if (not isinstance(heading.get("level"), int) or isinstance(heading["level"], bool)
                        or heading["level"] not in range(1, 7) or not isinstance(heading.get("page_no"), int)
                        or isinstance(heading["page_no"], bool) or heading["page_no"] < 1):
                    raise EvaluationError(f"Invalid heading level/page in case {case_id}")
        tags = case.get("tags", [])
        if not isinstance(tags, list) or any(not isinstance(tag, str) or tag not in TAGS for tag in tags):
            raise EvaluationError(f"Unsupported scenario tag in case {case_id}")
    if classes != CLASSES:
        raise EvaluationError("Suite must include all four built-in document classes")
    return cases


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        raise EvaluationError("Business API redirect refused")


class BusinessReader:
    """Bounded GET-only reader for a host-local or isolated-network business service."""

    def __init__(self, base_url: str) -> None:
        parsed = urlsplit(base_url)
        if (parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise EvaluationError("Use an explicit plain HTTP business API origin")
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            if "." in parsed.hostname and not parsed.hostname.endswith((".internal", ".local")):
                raise EvaluationError("Business API hostname must be internal") from None
        else:
            if not (address.is_private or address.is_loopback):
                raise EvaluationError("Business API address must be private or loopback")
        self.base_url = base_url.rstrip("/")
        self.opener = build_opener(_NoRedirect())

    def get(self, path: str) -> Any:
        if not path.startswith("/") or ".." in path:
            raise EvaluationError("Invalid business API path")
        request = Request(f"{self.base_url}/api/business{path}", headers={"Accept": "application/json"})
        try:
            with self.opener.open(request, timeout=20) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except OSError as exc:
            raise EvaluationError("Business API read failed") from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise EvaluationError("Business API response exceeds evaluation limit")
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise EvaluationError("Business API returned non-JSON data") from exc


def _outline(revision_id: str, get: Callable[[str], Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    next_page: int | None = None
    scanned = 0
    while True:
        suffix = f"?start_page={next_page}" if next_page is not None else ""
        page = get(f"/revisions/{quote(revision_id, safe='')}/outline{suffix}")
        if not isinstance(page, dict) or page.get("revision_id") != revision_id:
            raise EvaluationError("Outline response identity mismatch")
        if not isinstance(page.get("items"), list) or not isinstance(page.get("scanned_pages"), int):
            raise EvaluationError("Invalid outline response")
        items.extend(page["items"])
        scanned += page["scanned_pages"]
        if scanned > 1000:
            raise EvaluationError("Outline exceeds evaluation page limit")
        upcoming = page.get("next_page")
        if upcoming is None:
            return items
        if not isinstance(upcoming, int) or (next_page is not None and upcoming <= next_page):
            raise EvaluationError("Invalid outline continuation")
        next_page = upcoming


def evaluate_case(case: dict[str, Any], get: Callable[[str], Any]) -> dict[str, Any]:
    """Score exact candidate values and frozen evidence without returning raw annotations."""
    case_id = case["id"]
    document_id, revision_id, run_id = (case[key] for key in ("document_id", "revision_id", "run_id"))
    document = get(f"/documents/{quote(document_id, safe='')}")
    revisions = get(f"/documents/{quote(document_id, safe='')}/revisions")
    extraction = get(f"/extractions/{quote(run_id, safe='')}")
    evidence = get(f"/revisions/{quote(revision_id, safe='')}/evidence")
    if (
        not isinstance(document, dict) or document.get("id") != document_id
        or document.get("sha256") != case["sha256"] or document.get("template_code") != case["category"]
        or not isinstance(revisions, list) or not any(
            isinstance(revision, dict) and revision.get("id") == revision_id
            and revision.get("document_id") == document_id for revision in revisions
        )
        or not isinstance(extraction, dict) or not isinstance(extraction.get("run"), dict)
        or extraction["run"].get("id") != run_id or extraction["run"].get("revision_id") != revision_id
        or extraction["run"].get("template_code") != case["category"]
        or extraction["run"].get("status") != "done"
        or not isinstance(extraction.get("candidates"), list) or not isinstance(extraction.get("issues"), list)
        or not isinstance(evidence, list)
    ):
        raise EvaluationError(f"Business identity or extraction state mismatch in case {case_id}")
    candidates = extraction["candidates"]
    snapshots = {
        item["id"]: item for item in evidence
        if isinstance(item, dict) and isinstance(item.get("id"), str)
        and item.get("revision_id") == revision_id and item.get("document_id") == document_id
    }
    used: set[int] = set()
    matched = 0
    evidence_hits = 0
    for field in case["expected_fields"]:
        index = next((index for index, candidate in enumerate(candidates) if index not in used
                      and isinstance(candidate, dict) and candidate.get("field_code") == field["code"]
                      and candidate.get("value") == field["value"]), None)
        if index is None:
            continue
        used.add(index)
        matched += 1
        candidate = candidates[index]
        snapshot = snapshots.get(candidate.get("evidence_id"))
        if snapshot is None or not isinstance(snapshot.get("snippet"), str):
            continue
        if hashlib.sha256(snapshot["snippet"].encode("utf-8")).hexdigest() != snapshot.get("snippet_sha256"):
            raise EvaluationError(f"Frozen evidence checksum mismatch in case {case_id}")
        if field["value"] not in snapshot["snippet"]:
            continue
        if "evidence_quote" in field and field["evidence_quote"] not in snapshot["snippet"]:
            continue
        if "page_no" in field and snapshot.get("page_no") != field["page_no"]:
            continue
        evidence_hits += 1
    report: dict[str, Any] = {
        "id": case_id, "category": case["category"], "tags": case.get("tags", []), "document_id": document_id,
        "revision_id": revision_id, "run_id": run_id,
        "expected_fields": len(case["expected_fields"]), "matched_fields": matched,
        "candidate_fields": len(candidates), "matched_candidates": len(used),
        "evidence_hits": evidence_hits,
        "open_blocking_issues": sum(1 for issue in extraction["issues"] if isinstance(issue, dict)
                                    and issue.get("severity") == "blocking" and issue.get("status") == "open"),
    }
    if "expected_headings" in case:
        expected = Counter((item["title"], item["level"], item["page_no"]) for item in case["expected_headings"])
        observed = Counter((item.get("title"), item.get("level"), item.get("page_no"))
                           for item in _outline(revision_id, get))
        report["expected_headings"] = sum(expected.values())
        report["matched_headings"] = sum((expected & observed).values())
        report["observed_headings"] = sum(observed.values())
    return report


def evaluate_suite(cases: list[dict[str, Any]], get: Callable[[str], Any], environment: str) -> dict[str, Any]:
    reports = [evaluate_case(case, get) for case in cases]
    names = ("expected_fields", "matched_fields", "candidate_fields", "matched_candidates",
             "evidence_hits", "open_blocking_issues", "expected_headings", "matched_headings", "observed_headings")

    def totals(items: list[dict[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {name: sum(item.get(name, 0) for item in items) for name in names}
        result["field_recall"] = round(result["matched_fields"] / result["expected_fields"], 4) \
            if result["expected_fields"] else None
        result["candidate_precision"] = round(result["matched_candidates"] / result["candidate_fields"], 4) \
            if result["candidate_fields"] else None
        result["evidence_hit_rate"] = round(result["evidence_hits"] / result["expected_fields"], 4) \
            if result["expected_fields"] else None
        result["heading_recall"] = round(result["matched_headings"] / result["expected_headings"], 4) \
            if result["expected_headings"] else None
        return result

    aggregate = totals(reports)
    by_class = {category: totals([report for report in reports if report["category"] == category])
                for category in sorted(CLASSES)}
    by_tag = {tag: totals([report for report in reports if tag in report["tags"]]) for tag in sorted(TAGS)
              if any(tag in report["tags"] for report in reports)}
    return {"schema": 1, "environment": environment, "case_count": len(reports),
            "classes": sorted({report["category"] for report in reports}),
            "cases": reports, "aggregate": aggregate, "by_class": by_class, "by_tag": by_tag,
            "note": "Exact-match machine candidate metrics; not human-confirmed accuracy or production acceptance."}


def _write_new_report(output: Path, report: dict[str, Any]) -> None:
    """Publish a complete report only if this path has never been used."""
    if output.exists() or output.is_symlink():
        raise EvaluationError("Evaluation report already exists; choose a new output path")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output.parent, prefix=f".{output.name}.", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, output)
        except FileExistsError as exc:
            raise EvaluationError("Evaluation report already exists; choose a new output path") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite", type=Path, required=True,
        help="External four-class annotation JSON; never commit raw samples",
    )
    parser.add_argument("--base-url", required=True, help="Approved isolated business Web/API origin")
    parser.add_argument("--environment", required=True, help="Actual run environment, e.g. Mac or Kylin GPU identity")
    parser.add_argument("--output", type=Path, required=True, help="Redacted metric report path outside the sample tree")
    args = parser.parse_args()
    try:
        cases = load_suite(args.suite)
        if args.output.resolve().is_relative_to(args.suite.parent.resolve()):
            raise EvaluationError("Report must be outside the sample directory")
        if args.output.exists() or args.output.is_symlink():
            raise EvaluationError("Evaluation report already exists; choose a new output path")
        report = evaluate_suite(cases, BusinessReader(args.base_url).get,
                                _required_string(args.environment, "environment"))
        _write_new_report(args.output, report)
    except (EvaluationError, OSError) as exc:
        print(f"Sample evaluation failed: {exc}", file=sys.stderr)
        return 1
    print(f"Evaluated {report['case_count']} cases; redacted report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
