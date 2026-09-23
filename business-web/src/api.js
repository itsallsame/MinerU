const base = "/api/business";

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(`${base}${path}`, { cache: "no-store", ...options });
  } catch {
    throw new ApiError("无法连接业务服务，请检查服务是否运行及内网地址。", 0);
  }
  if (!response.ok) {
    let detail = `请求失败（${response.status}）`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // Preserve the HTTP status when the server returns a non-JSON failure.
    }
    throw new ApiError(detail, response.status);
  }
  return response.json();
}

const postJson = (path, body) => request(path, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const businessApi = {
  capabilities: () => request("/capabilities"),
  templates: () => request("/templates"),
  search: (query, limit = 20) => request(`/search?${new URLSearchParams({ query, limit: String(limit) })}`),
  readRevision: (id, locator, limit = 12000) => request(
    `/revisions/${encodeURIComponent(id)}/content?${new URLSearchParams({ locator, limit: String(limit) })}`,
  ),
  searchRevision: (id, query, startPage = null) => request(
    `/revisions/${encodeURIComponent(id)}/search?${new URLSearchParams({
      query, ...(startPage === null ? {} : { start_page: String(startPage) }),
    })}`,
  ),
  documents: ({ limit = 20, offset = 0, status = "", templateCode = "" } = {}) => {
    const query = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    if (status) query.set("status", status);
    if (templateCode) query.set("template_code", templateCode);
    return request(`/documents?${query}`);
  },
  document: (id) => request(`/documents/${encodeURIComponent(id)}`),
  revisions: (id) => request(`/documents/${encodeURIComponent(id)}/revisions`),
  revisionEvidence: (id) => request(`/revisions/${encodeURIComponent(id)}/evidence`),
  captureEvidence: (id, locator) => postJson(`/revisions/${encodeURIComponent(id)}/evidence`, { locator }),
  inspectEvidence: (id) => request(`/evidence/${encodeURIComponent(id)}`),
  extractions: (id) => request(`/revisions/${encodeURIComponent(id)}/extractions`),
  enqueueExtraction: (id) => request(`/revisions/${encodeURIComponent(id)}/extractions`, { method: "POST" }),
  extraction: (id) => request(`/extractions/${encodeURIComponent(id)}`),
  templateVersion: (code, version) => request(`/templates/${encodeURIComponent(code)}?version=${encodeURIComponent(version)}`),
  decisions: (id) => request(`/extractions/${encodeURIComponent(id)}/decisions`),
  decideField: (runId, fieldCode, value, evidenceId, reason = null) => postJson(
    `/extractions/${encodeURIComponent(runId)}/fields/${encodeURIComponent(fieldCode)}/decisions`,
    { value, evidence_id: evidenceId, source: "web", reason },
  ),
  resolveIssue: (issueId, status, reason) => postJson(
    `/issues/${encodeURIComponent(issueId)}/resolutions`, { status, source: "web", reason },
  ),
  results: (id) => request(`/extractions/${encodeURIComponent(id)}/results`),
  confirm: (id) => postJson(`/extractions/${encodeURIComponent(id)}/confirm`, { source: "web" }),
  audit: (id) => request(`/extractions/${encodeURIComponent(id)}/audit`),
  task: (id) => request(`/tasks/${encodeURIComponent(id)}`),
  retry: (id) => request(`/tasks/${encodeURIComponent(id)}/retry`, { method: "POST" }),
  upload: (file, { tier, templateCode } = {}) => {
    const body = new FormData();
    body.append("file", file, file.name);
    if (tier) body.append("tier", tier);
    if (templateCode) body.append("template_code", templateCode);
    return request("/documents", { method: "POST", body });
  },
  sourceUrl: (id) => `${base}/documents/${encodeURIComponent(id)}/source`,
};
