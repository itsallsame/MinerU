---
name: business-documents
description: "Use this deployment's open MinerU business document platform to submit and search documents, read a historical revision by locator, inspect frozen evidence, and distinguish machine drafts from confirmed results. Use for this project's business workflow, not the upstream MinerU CLI or official Skill."
---

# Business Documents

This is the Skill for the **fork's business platform**, not the upstream `skills/mineru` Skill. Use its bundled `scripts/business_documents.py` as the only document-platform entrypoint. It calls `/api/business` on the same service as the business Web; never call Doclib, the upstream MinerU CLI/API, model servers, local data paths, or a remote parsing service to satisfy a business-platform request.

Set `MINERU_BUSINESS_API_URL` to the approved business Web/API address, for example `http://127.0.0.1:8080` when running on the host. The script rejects public IP literals, non-internal hostnames, and HTTPS/credential URLs; use a loopback/private address or an approved internal service name. A hostname suffix alone does not prove network isolation. The service is **open**: there is no login, user identity, role, token, or permission check. Network reachability is the deployment boundary. Confirm the configured address is the intended isolated service before uploading a document; do not invent an internet endpoint or silently switch servers.

For Agent installation, copy this `skills/business-documents/` folder into the Agent's skills directory. On a connected preparation machine, a compatible Skills manager may instead install the `business-documents` folder from this fork; transfer the pinned folder and script into the isolated deployment by approved media. Never run a network installer on the production host.

Run `python3 skills/business-documents/scripts/business_documents.py --help` for the supported commands. The script uses only the Python standard library and prints JSON. A typical flow is:

```bash
python3 skills/business-documents/scripts/business_documents.py capabilities
python3 skills/business-documents/scripts/business_documents.py templates
python3 skills/business-documents/scripts/business_documents.py upload /path/to/notice.pdf --tier standard --template official_document
python3 skills/business-documents/scripts/business_documents.py task TASK_ID
python3 skills/business-documents/scripts/business_documents.py overview DOCUMENT_ID
python3 skills/business-documents/scripts/business_documents.py extract REVISION_ID
python3 skills/business-documents/scripts/business_documents.py extraction RUN_ID
python3 skills/business-documents/scripts/business_documents.py search "年度通知" --limit 20
python3 skills/business-documents/scripts/business_documents.py search-pages REVISION_ID "年度通知"
python3 skills/business-documents/scripts/business_documents.py outline REVISION_ID
python3 skills/business-documents/scripts/business_documents.py structure REVISION_ID 1
python3 skills/business-documents/scripts/business_documents.py read REVISION_ID 'doc:SHORT_ID/tier:flash/page:1'
python3 skills/business-documents/scripts/business_documents.py evidence EVIDENCE_ID
python3 skills/business-documents/scripts/business_documents.py results RUN_ID
```

Upload requires an explicit local file and uses the business service's advertised size/extension/tier limits. PDF/images accept `flash`, `basic`, `standard`, or `advanced`; other supported native formats use Flash with no `--tier`. A response with `submitted`, `queued`, or `running` is **not** a finished parse/extraction; poll the returned task/run ID rather than guessing a completion time. Do not auto-retry failed writes or upload the same file again without the user's direction.

`extraction` returns **machine candidates, not confirmed facts**. Clearly label any answer based on candidates as “机器候选，未人工确认”; include the field, source evidence ID, and unresolved issues. Do not turn a candidate into a confirmed statement merely because it has a locator. `results` and `overview.confirmed_for_latest_run` read immutable **human-confirmed** versions only. `overview` selects the newest parse revision and newest extraction run; if its confirmed result is null, say that **this run** has no confirmed result, not that the document has never had one. Use `revisions` and `results` to inspect older runs. Do not run field decisions, issue resolutions, or result confirmation automatically: those are explicit review steps in the business Web, even though the open API has no permission layer.

`evidence` returns the frozen original snippet, `navigation_status`, and `web_url` for opening that evidence in the same business Web. Cite the evidence ID and locator; include the Web link when the user needs to inspect it. `current_match` means current content matches the frozen snippet; it does **not** prove the same historical parse batch. With `changed` or `unavailable`, present the frozen snippet but do not claim a live jump to its historical location. A raw source download is not a substitute for a reviewed result.

`search` is business-scoped discovery: returned snippets are **current index previews, unconfirmed**, not frozen evidence or citation-ready text. Only hits mapped to a business document with a completed matching-tier revision are returned; `scan_complete` concerns the Doclib-reported result window, not guaranteed full-corpus recall. `search-pages` scans at most 25 pages of a **specified historical business revision** per call and returns page locators with `historical_parse_unconfirmed` snippets. Continue from `next_page` with `--start-page` to cover a longer document; a missing hit in one window is not a full-document negative result. A truncated page fails instead of silently claiming no match. Page hits are machine text, not frozen evidence or block-level citations. `read` takes a **business revision ID and its matching locator**; it reads that historical parse batch, returns `next_locator` for bounded continuation, and labels the text `historical_parse_unconfirmed`. It is not an immutable evidence snapshot or human-confirmed field. For a final citation, use a frozen evidence ID or confirmed field evidence, not a search preview.

`outline` derives a heading outline from the specified historical revision's parsed Markdown, scanning at most 25 pages per call. Use `--start-page` with `next_page` until the needed range is covered. Heading level and page locator are machine-derived, unconfirmed, and only page-precise; absence of headings does not mean the source has no semantic structure. Do not cite outline labels as frozen evidence.

`structure` reads the native parent/child model block tree for one page of the specified historical parse revision. Each node has a structural `path`, type, optional heading level, short text preview and available bbox. Children inherit their top-level block's locator; this is an **ancestor anchor**, not an exact child locator. Inspect the top-level block with `read` and use frozen evidence for final citations. All nodes are machine-parsed, not reviewed evidence.

Current boundary: the tree reflects nodes actually present in MinerU's model schema; do not invent table cells or exact child coordinates the model did not provide. A block-level search evidence list is still pending. Do not silently fall back to Doclib/official Skill; tell the user when requested evidence is unavailable.

HTTP errors print their actual status and business-service detail. On 404, recheck the business ID; on 409, report the conflict or stale source; on 503, report the unavailable business/Doclib worker. Do not hide errors or infer a successful parse/result from a successful upload.
