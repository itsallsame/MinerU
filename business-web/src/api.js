const base = "/api/business";

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request(path, options = {}, expectJson = true) {
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
  return expectJson ? response.json() : null;
}

const postJson = (path, body) => request(path, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const businessApi = {
  capabilities: () => request("/capabilities"),
  qualityStats: () => request("/quality-stats"),
  auditPage: (before = null, limit = 20) => request(`/audit?${new URLSearchParams({
    limit: String(limit), ...(before ? { before } : {}),
  })}`),
  templates: () => request("/templates"),
  createTemplate: (definition, requestKey) => request("/templates", {
    method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": requestKey },
    body: JSON.stringify(definition),
  }),
  updateTemplate: (code, definition, requestKey, expectedVersion) => request(`/templates/${encodeURIComponent(code)}`, {
    method: "PUT", headers: {
      "Content-Type": "application/json", "Idempotency-Key": requestKey, "If-Match": `"${expectedVersion}"`,
    },
    body: JSON.stringify(definition),
  }),
  disableTemplate: (code, requestKey, expectedVersion) => request(`/templates/${encodeURIComponent(code)}/disable`, {
    method: "POST", headers: { "Idempotency-Key": requestKey, "If-Match": `"${expectedVersion}"` },
  }),
  templateRequest: (requestKey) => request(`/template-requests/${encodeURIComponent(requestKey)}`),
  search: (query, limit = 20) => request(`/search?${new URLSearchParams({ query, limit: String(limit) })}`),
  readRevision: (id, locator, limit = 12000) => request(
    `/revisions/${encodeURIComponent(id)}/content?${new URLSearchParams({ locator, limit: String(limit) })}`,
  ),
  diffRevisions: (id, otherId, startPage = null) => request(
    `/revisions/${encodeURIComponent(id)}/diff?${new URLSearchParams({
      other_revision_id: otherId, ...(startPage === null ? {} : { start_page: String(startPage) }),
    })}`,
  ),
  searchRevision: (id, query, startPage = null) => request(
    `/revisions/${encodeURIComponent(id)}/search?${new URLSearchParams({
      query, ...(startPage === null ? {} : { start_page: String(startPage) }),
    })}`,
  ),
  searchBlocks: (id, query, startPage = null) => request(
    `/revisions/${encodeURIComponent(id)}/search-blocks?${new URLSearchParams({
      query, ...(startPage === null ? {} : { start_page: String(startPage) }),
    })}`,
  ),
  outline: (id, startPage = null) => request(
    `/revisions/${encodeURIComponent(id)}/outline${startPage === null ? "" : `?${new URLSearchParams({ start_page: String(startPage) })}`}`,
  ),
  structure: (id, pageNo) => request(
    `/revisions/${encodeURIComponent(id)}/structure?${new URLSearchParams({ page_no: String(pageNo) })}`,
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
  enqueueExtraction: (id, requestKey) => request(`/revisions/${encodeURIComponent(id)}/extractions`, {
    method: "POST", headers: { "Idempotency-Key": requestKey },
  }),
  extractionRequest: (key) => request(`/extraction-requests/${encodeURIComponent(key)}`),
  extraction: (id) => request(`/extractions/${encodeURIComponent(id)}`),
  templateVersion: (code, version) => request(`/templates/${encodeURIComponent(code)}?version=${encodeURIComponent(version)}`),
  decisions: (id) => request(`/extractions/${encodeURIComponent(id)}/decisions`),
  decideField: (runId, fieldCode, value, evidenceId, reason = null, requestKey = null) => request(
    `/extractions/${encodeURIComponent(runId)}/fields/${encodeURIComponent(fieldCode)}/decisions`, {
      method: "POST", headers: { "Content-Type": "application/json", ...(requestKey ? { "Idempotency-Key": requestKey } : {}) },
      body: JSON.stringify({ value, evidence_id: evidenceId, source: "web", reason }),
    },
  ),
  resolveIssue: (issueId, status, reason, requestKey = null) => request(
    `/issues/${encodeURIComponent(issueId)}/resolutions`, {
      method: "POST", headers: { "Content-Type": "application/json", ...(requestKey ? { "Idempotency-Key": requestKey } : {}) },
      body: JSON.stringify({ status, source: "web", reason }),
    },
  ),
  results: (id) => request(`/extractions/${encodeURIComponent(id)}/results`),
  confirm: (id, requestKey, expectedDecisions, expectedIssues) => request(`/extractions/${encodeURIComponent(id)}/confirm`, {
    method: "POST", headers: { "Content-Type": "application/json", ...(requestKey ? { "Idempotency-Key": requestKey } : {}) },
    body: JSON.stringify({ source: "web", expected_decisions: expectedDecisions, expected_issues: expectedIssues }),
  }),
  reviewRequest: (key) => request(`/review-requests/${encodeURIComponent(key)}`),
  audit: (id) => request(`/extractions/${encodeURIComponent(id)}/audit`),
  task: (id) => request(`/tasks/${encodeURIComponent(id)}`),
  retry: (id, requestKey) => request(`/tasks/${encodeURIComponent(id)}/retry`, {
    method: "POST", headers: { "Idempotency-Key": requestKey },
  }),
  taskRetryRequest: (key) => request(`/task-retry-requests/${encodeURIComponent(key)}`),
  cancel: (id, requestKey) => request(`/tasks/${encodeURIComponent(id)}/cancel`, {
    method: "POST", headers: { "Idempotency-Key": requestKey },
  }),
  taskCancelRequest: (key) => request(`/task-cancel-requests/${encodeURIComponent(key)}`),
  uploadRequest: (key) => request(`/upload-requests/${encodeURIComponent(key)}`),
  upload: (file, { tier, templateCode, requestKey } = {}) => {
    const body = new FormData();
    body.append("file", file, file.name);
    if (tier) body.append("tier", tier);
    if (templateCode) body.append("template_code", templateCode);
    return request("/documents", {
      method: "POST", body, headers: requestKey ? { "Idempotency-Key": requestKey } : {},
    });
  },
  sourceUrl: (id) => `${base}/documents/${encodeURIComponent(id)}/source`,
  sourceAvailable: (id) => request(`/documents/${encodeURIComponent(id)}/source`, { method: "HEAD" }, false),
};
