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

export const businessApi = {
  capabilities: () => request("/capabilities"),
  templates: () => request("/templates"),
  documents: ({ limit = 20, offset = 0, status = "", templateCode = "" } = {}) => {
    const query = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    if (status) query.set("status", status);
    if (templateCode) query.set("template_code", templateCode);
    return request(`/documents?${query}`);
  },
  document: (id) => request(`/documents/${encodeURIComponent(id)}`),
  revisions: (id) => request(`/documents/${encodeURIComponent(id)}/revisions`),
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
