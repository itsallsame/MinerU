import { businessApi } from "./api.js";
import { classifyFile, extensionOf, formatBytes, sourcePreviewKind, taskLabel, tierForFile } from "./domain.js";
import { createReviewWorkbench } from "./review.js";
import { createTemplateManager } from "./templates.js";

const byId = (id) => document.getElementById(id);
const state = {
  capabilities: null,
  templates: [],
  page: 0,
  limit: 20,
  total: 0,
  items: [],
  selectedId: null,
  selectedSearchDocument: null,
  selectedReviewTarget: {},
  revisions: [],
  revisionsError: "",
  selectionRequest: 0,
  listRequest: 0,
  workspaceRequest: 0,
  searchRequest: 0,
  polling: false,
  uploading: false,
  auditRequest: 0,
  qualityRequest: 0,
  evidenceLinkRequest: 0,
  auditBefore: null,
  auditItems: [],
  sourcePageNo: 1,
  sourceStatus: "idle",
  sourceError: "",
  sourceRequest: 0,
};
const review = createReviewWorkbench(byId("workbench"), {
  onEvidenceNavigate: (evidence) => {
    const item = state.items.find((entry) => entry.document.id === state.selectedId)
      || (state.selectedSearchDocument ? { document: state.selectedSearchDocument } : null);
    if (!item) return false;
    const kind = sourcePreviewKind(item.document.original_name);
    if (kind === "pdf") {
      const frame = byId("detail-content").querySelector("iframe.source-preview");
      if (!frame) return false;
      state.sourcePageNo = evidence.page_no;
      const pageInput = byId("detail-content").querySelector('input[aria-label="查看 PDF 原文页码"]');
      if (pageInput) pageInput.value = String(evidence.page_no);
      frame.src = `${businessApi.sourceUrl(item.document.id)}#page=${evidence.page_no}`;
      review.showSourcePage(evidence.page_no);
      frame.scrollIntoView({ behavior: "smooth", block: "center" });
      return true;
    }
    if (kind === "image" && evidence.page_no === 1) {
      const preview = byId("detail-content").querySelector("img.source-preview");
      if (!preview) return false;
      state.sourcePageNo = 1;
      review.showSourcePage(1);
      preview.scrollIntoView({ behavior: "smooth", block: "center" });
      return true;
    }
    return false;
  },
});
const templateManager = createTemplateManager(byId("template-manager-content"), {
  onChanged: async () => {
    state.templates = await businessApi.templates();
    populateTemplateSelects();
    templateManager.setTemplates(state.templates);
    renderDocuments();
    renderDetail();
  },
});

function element(tag, className = "", content = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content) node.textContent = content;
  return node;
}

function setConnection(online, message) {
  const node = byId("connection");
  node.textContent = message;
  node.className = `connection ${online ? "online" : "offline"}`;
}

function showError(message) {
  const node = byId("global-error");
  node.textContent = message;
  node.hidden = false;
}

function clearError() {
  const node = byId("global-error");
  node.textContent = "";
  node.hidden = true;
}

async function refreshQualityStats() {
  const requestNumber = ++state.qualityRequest;
  const root = byId("quality-content");
  root.replaceChildren(element("p", "review-hint", "正在读取业务记录统计…"));
  try {
    const stats = await businessApi.qualityStats();
    if (requestNumber !== state.qualityRequest || !byId("quality-panel").open) return;
    const grid = element("div", "quality-grid");
    for (const [label, value] of [
      ["业务文档", stats.documents], ["解析待处理任务", stats.parse_pending],
      ["解析完成任务", stats.parse_done], ["解析失败任务", stats.parse_failed],
      ["解析修订", stats.revisions], ["提取待处理运行", stats.extraction_pending],
      ["提取完成运行", stats.extraction_done], ["提取失败运行", stats.extraction_failed],
      ["未处理阻断问题", stats.open_issues], ["已确认提取运行", stats.confirmed_runs],
      ["成果版本", stats.result_versions],
    ]) {
      const card = element("div", "quality-metric");
      card.append(element("strong", "", String(value)), element("span", "", label));
      grid.append(card);
    }
    root.replaceChildren(grid, element("p", "review-hint", "按业务记录累计；任务、修订、运行和成果版本可一对多。未处理问题按问题条目计。统计不代表字段准确率或人工评估结果。"));
  } catch (error) {
    if (requestNumber !== state.qualityRequest || !byId("quality-panel").open) return;
    root.replaceChildren(element("p", "error-banner", `统计不可用：${error.message}`));
  }
}

const auditActions = {
  field_decided: "字段决定",
  issue_resolved: "问题处理",
  result_confirmed: "成果确认",
};

function renderAudit() {
  const root = byId("audit-content");
  root.replaceChildren();
  if (!state.auditItems.length) root.append(element("p", "review-hint", "暂无审计记录。"));
  for (const record of state.auditItems) {
    const event = record.event;
    const card = element("article", "audit-record");
    card.append(element("strong", "", `${record.document_name} · ${auditActions[event.action] || event.action}`));
    card.append(element("p", "review-hint", `${new Date(event.created_at_ms).toLocaleString("zh-CN")} · 入口：${event.source} · 运行 ${event.run_id.slice(0, 12)}…`));
    const detail = element("details");
    detail.append(element("summary", "", "查看变更与原因"));
    detail.append(element("p", "audit-value", `原值：${event.old_value ?? "（无）"}`));
    detail.append(element("p", "audit-value", `新值：${event.new_value}`));
    if (event.reason) detail.append(element("p", "audit-value", `原因：${event.reason}`));
    card.append(detail);
    const open = element("button", "secondary-button", "打开对应提取运行");
    open.type = "button";
    open.addEventListener("click", async () => {
      open.disabled = true;
      const selectionRequest = state.selectionRequest;
      try {
        const documentRecord = await businessApi.document(record.document_id);
        if (selectionRequest !== state.selectionRequest) return;
        await selectDocument(record.document_id, documentRecord, {
          revisionId: record.revision_id, runId: event.run_id,
        });
        if (state.selectionRequest !== selectionRequest + 1 || state.selectedId !== record.document_id) return;
        byId("workbench").scrollIntoView({ behavior: "smooth", block: "start" });
      } catch (error) {
        if (selectionRequest === state.selectionRequest
          || (state.selectionRequest === selectionRequest + 1 && state.selectedId === record.document_id)) {
          showError(`审计记录无法打开：${error.message}`);
        }
      } finally {
        open.disabled = false;
      }
    });
    card.append(open);
    root.append(card);
  }
  byId("audit-more").hidden = !state.auditBefore;
}

async function loadAudit(reset = false) {
  const requestNumber = ++state.auditRequest;
  const before = reset ? null : state.auditBefore;
  const more = byId("audit-more");
  more.disabled = true;
  if (reset) byId("audit-content").replaceChildren(element("p", "review-hint", "正在读取审计记录…"));
  else renderAudit();
  try {
    const page = await businessApi.auditPage(before);
    if (requestNumber !== state.auditRequest || !byId("audit-panel").open) return;
    state.auditItems = reset ? page.items : [...state.auditItems, ...page.items];
    state.auditBefore = page.next_before;
    renderAudit();
  } catch (error) {
    if (requestNumber !== state.auditRequest || !byId("audit-panel").open) return;
    const root = byId("audit-content");
    if (reset) {
      state.auditItems = [];
      state.auditBefore = null;
      root.replaceChildren();
      more.hidden = true;
    }
    root.append(element("p", "error-banner", `审计记录不可用：${error.message}${reset ? "" : "；已加载记录保留，后续页尚未读取。"}`));
    const retry = element("button", "secondary-button", reset ? "重试读取审计记录" : "重试加载这一页");
    retry.type = "button";
    retry.addEventListener("click", () => loadAudit(reset));
    root.append(retry);
  } finally {
    if (requestNumber === state.auditRequest) more.disabled = false;
  }
}

function updateFileSelection() {
  const files = [...byId("files").files];
  byId("selected-files").textContent = files.length
    ? `${files.length} 个文件 · ${files.map((file) => file.name).join("、")}`
    : "尚未选择文件";
  byId("upload-submit").disabled = !state.capabilities || !files.length || state.uploading;
  byId("files").disabled = state.uploading;
  byId("upload-template").disabled = state.uploading;
  byId("upload-tier").disabled = state.uploading;
}

function feedback(file, message, kind = "") {
  const node = element("div", `feedback-item ${kind}`, `${file.name} · ${message}`);
  byId("upload-feedback").append(node);
  return node;
}

function populateTemplateSelects() {
  const upload = byId("upload-template");
  const filter = byId("template-filter");
  for (const select of [upload, filter]) {
    const previous = select.value;
    select.replaceChildren(select.firstElementChild);
    for (const template of state.templates) {
      const option = element("option", "", template.name);
      option.value = template.code;
      option.disabled = select === upload && !template.enabled;
      select.append(option);
    }
    if ([...select.options].some((option) => option.value === previous && !option.disabled)) select.value = previous;
  }
}

async function refreshWorkspace() {
  const requestNumber = ++state.workspaceRequest;
  const [capabilities, templates] = await Promise.allSettled([businessApi.capabilities(), businessApi.templates()]);
  if (requestNumber !== state.workspaceRequest) return;
  const failures = [];
  if (capabilities.status === "fulfilled") {
    state.capabilities = capabilities.value;
    const cap = state.capabilities;
    byId("capability-note").textContent =
      `当前服务支持 ${cap.parseable_extensions.length} 种扩展名，单文件上限 ${formatBytes(cap.max_upload_bytes)}；` +
      "PDF / 图片提供四档解析。";
  } else {
    state.capabilities = null;
    byId("capability-note").textContent = "服务能力不可用，上传已暂停。";
    failures.push(`服务能力不可用：${capabilities.reason.message}`);
  }
  if (templates.status === "fulfilled") {
    state.templates = templates.value;
    populateTemplateSelects();
    templateManager.setTemplates(state.templates);
  } else {
    state.templates = [];
    populateTemplateSelects();
    templateManager.setTemplates(state.templates);
    failures.push(`模板列表暂不可用：${templates.reason.message}`);
  }
  updateFileSelection();
  const listLoaded = await refreshDocuments();
  if (requestNumber === state.workspaceRequest && listLoaded && failures.length) showError(failures.join("；"));
}

async function bootstrap() {
  await refreshWorkspace();
  await openEvidenceLink();
}

async function openEvidenceLink() {
  const requestNumber = ++state.evidenceLinkRequest;
  const selectionRequest = state.selectionRequest;
  const match = /^#evidence=([A-Za-z0-9_-]{1,100})$/.exec(window.location.hash);
  if (!match) return;
  let targetDocumentId = null;
  try {
    const evidence = await businessApi.inspectEvidence(match[1]);
    if (requestNumber !== state.evidenceLinkRequest || selectionRequest !== state.selectionRequest) return;
    targetDocumentId = evidence.document_id;
    const record = await businessApi.document(evidence.document_id);
    if (requestNumber !== state.evidenceLinkRequest || selectionRequest !== state.selectionRequest) return;
    await selectDocument(record.id, record, { revisionId: evidence.revision_id, evidenceId: evidence.id });
    if (requestNumber !== state.evidenceLinkRequest || state.selectionRequest !== selectionRequest + 1
      || state.selectedId !== record.id) return;
    state.sourcePageNo = evidence.page_no;
    renderDetail();
    review.showSourcePage(evidence.page_no);
  } catch (error) {
    if (requestNumber === state.evidenceLinkRequest
      && (selectionRequest === state.selectionRequest
        || (state.selectionRequest === selectionRequest + 1 && state.selectedId === targetDocumentId))) {
      showError(`证据链接不可用：${error.message}`);
    }
  }
}

function renderDocuments() {
  const container = byId("document-list");
  const focusedDocumentId = container.contains(document.activeElement)
    ? document.activeElement.dataset.documentId : null;
  container.replaceChildren();
  byId("total-count").textContent = `${state.total} 份文档`;
  if (!state.items.length) {
    container.append(element("div", "empty-state", "当前筛选下还没有文档。可以从左侧添加第一份文档。"));
  }
  for (const item of state.items) {
    const { document: record, task } = item;
    const card = element("button", `document-card ${record.id === state.selectedId ? "selected" : ""}`);
    card.type = "button";
    card.dataset.documentId = record.id;
    card.setAttribute("aria-current", record.id === state.selectedId ? "true" : "false");
    card.setAttribute("aria-label", `查看 ${record.original_name}，${task ? taskLabel(task.status) : "无任务"}`);
    const icon = element("span", "file-icon", extensionOf(record.original_name).slice(0, 4) || "FILE");
    icon.setAttribute("aria-hidden", "true");
    const body = element("span", "document-card-content");
    body.append(element("strong", "", record.original_name));
    const template = state.templates.find((entry) => entry.code === record.template_code);
    body.append(element("span", "document-meta", `${template?.name || "未分类"} · ${formatBytes(record.size)} · ${new Date(record.created_at_ms).toLocaleString("zh-CN")}`));
    const badge = element("span", `status ${task?.status || ""}`, task ? taskLabel(task.status) : "无任务");
    card.append(icon, body, badge);
    card.addEventListener("click", () => selectDocument(record.id));
    container.append(card);
  }
  if (focusedDocumentId) {
    const restored = [...container.querySelectorAll("button[data-document-id]")]
      .find((card) => card.dataset.documentId === focusedDocumentId);
    restored?.focus({ preventScroll: true });
  }
  byId("previous-page").disabled = state.page === 0;
  byId("next-page").disabled = (state.page + 1) * state.limit >= state.total;
  byId("page-label").textContent = `第 ${state.page + 1} 页`;
}

async function refreshDocuments() {
  const requestNumber = ++state.listRequest;
  try {
    const page = await businessApi.documents({
      limit: state.limit,
      offset: state.page * state.limit,
      status: byId("status-filter").value,
      templateCode: byId("template-filter").value,
    });
    if (requestNumber !== state.listRequest) return false;
    state.items = page.items;
    state.total = page.total;
    setConnection(true, "业务服务已连接");
    clearError();
    if (state.selectedId && !state.selectedSearchDocument && !state.items.some((item) => item.document.id === state.selectedId)) {
      state.selectedId = null;
      state.selectionRequest += 1;
      state.sourceRequest += 1;
      state.sourceStatus = "idle";
      state.revisions = [];
      state.revisionsError = "";
      review.clear();
      const detail = byId("detail-content");
      detail.className = "detail-empty";
      detail.replaceChildren(
        element("h2", "", "选择一份文档"),
        element("p", "", "这里会展示原文、解析任务与修订记录。"),
      );
    }
    renderDocuments();
    if (state.selectedId) renderDetail();
    return true;
  } catch (error) {
    if (requestNumber !== state.listRequest) return false;
    setConnection(false, "业务服务不可用");
    showError(error.message);
    state.capabilities = null;
    byId("capability-note").textContent = "文档库不可用，上传已暂停；重新连接后会再次读取服务能力。";
    updateFileSelection();
    state.items = [];
    state.total = 0;
    state.selectedId = null;
    state.selectedSearchDocument = null;
    state.selectionRequest += 1;
    state.sourceRequest += 1;
    state.sourceStatus = "idle";
    state.revisionsError = "";
    state.revisions = [];
    review.clear();
    byId("total-count").textContent = "文档数量未知";
    byId("previous-page").disabled = true;
    byId("next-page").disabled = true;
    byId("detail-content").className = "detail-empty";
    byId("detail-content").replaceChildren(
      element("h2", "", "文档状态暂不可用"),
      element("p", "", "连接恢复后重新读取文档，旧状态不会作为当前结果展示。"),
    );
    const retry = element("button", "secondary-button", "重新连接并刷新");
    retry.type = "button";
    retry.addEventListener("click", refreshWorkspace);
    byId("document-list").replaceChildren(
      element("div", "empty-state", "无法读取文档。请检查业务服务或内网地址。"), retry,
    );
    return false;
  }
}

function detailRow(label, value) {
  const row = element("div");
  row.append(element("span", "", label), element("strong", "", value));
  return row;
}

async function checkSource(id) {
  const requestNumber = ++state.sourceRequest;
  state.sourceStatus = "checking";
  state.sourceError = "";
  renderDetail();
  try {
    await businessApi.sourceAvailable(id);
    if (requestNumber !== state.sourceRequest || id !== state.selectedId) return;
    state.sourceStatus = "available";
  } catch (error) {
    if (requestNumber !== state.sourceRequest || id !== state.selectedId) return;
    state.sourceStatus = "unavailable";
    state.sourceError = error.status === 409
      ? "原件缺失或内容与上传记录不一致，不能预览或下载。"
      : error.status === 404
        ? "业务文档已不存在，原件无法读取。"
        : `原件暂不可用（${error.status || "连接失败"}），请检查业务服务后重试。`;
  }
  renderDetail();
}

function renderDetail() {
  const item = state.items.find((entry) => entry.document.id === state.selectedId)
    || (state.selectedSearchDocument ? { document: state.selectedSearchDocument, task: null } : null);
  if (!item) return;
  const { document: record, task } = item;
  const root = byId("detail-content");
  root.className = "";
  root.replaceChildren();
  const top = element("div", "detail-topline");
  top.append(element("span", "eyebrow", "DOCUMENT DETAIL"), element("span", "detail-id", record.id.slice(0, 12)));
  root.append(top, element("h2", "", record.original_name));
  root.append(element("p", "detail-subline", `原文件 SHA-256：${record.sha256.slice(0, 16)}…`));
  const summary = element("section", "detail-section");
  summary.append(element("h3", "", "处理状态"));
  const grid = element("div", "detail-grid");
  const template = state.templates.find((entry) => entry.code === record.template_code);
  grid.append(
    detailRow("业务类型", template?.name || "未分类"),
    detailRow("解析状态", task ? taskLabel(task.status) : state.selectedSearchDocument ? "检索未返回任务状态" : "无任务"),
    detailRow("请求档位", task?.requested_tier || "Flash（原生格式）"),
    detailRow("实际档位", task?.actual_tier || state.revisions[0]?.tier || "尚未完成"),
  );
  summary.append(grid);
  if (task?.error_code) summary.append(element("p", "error-banner", `失败代码：${task.error_code}`));
  const actions = element("div", "detail-actions");
  if (task?.status === "failed") {
    const retry = element("button", "", "重新提交任务");
    retry.type = "button";
    retry.addEventListener("click", async () => {
      retry.disabled = true;
      const selectionRequest = state.selectionRequest;
      try {
        await businessApi.retry(task.id);
        if (selectionRequest === state.selectionRequest) clearError();
        await refreshDocuments();
      } catch (error) {
        if (selectionRequest === state.selectionRequest) showError(`重试失败：${error.message}`);
        retry.disabled = false;
      }
    });
    actions.append(retry);
  }
  summary.append(actions);
  root.append(summary);

  const source = element("section", "detail-section");
  source.append(element("h3", "", "原文"));
  const sourceUrl = businessApi.sourceUrl(record.id);
  const kind = sourcePreviewKind(record.original_name);
  if (state.sourceStatus !== "available") {
    source.append(element("p", state.sourceStatus === "unavailable" ? "error-banner" : "review-hint",
      state.sourceStatus === "unavailable" ? state.sourceError : "正在核验原件完整性…"));
    if (state.sourceStatus === "unavailable") {
      const retrySource = element("button", "secondary-button", "重试核验原件");
      retrySource.type = "button";
      retrySource.addEventListener("click", () => checkSource(record.id));
      source.append(retrySource);
    }
  } else {
    const download = element("a", "", kind === "download" ? "下载原文件 ↗" : "在新窗口打开原文 ↗");
    download.href = sourceUrl;
    download.target = "_blank";
    download.rel = "noopener noreferrer";
    const sourceActions = element("div", "detail-actions");
    sourceActions.append(download);
    source.append(sourceActions);
    if (kind === "pdf") {
      const pageForm = element("form", "source-page-form");
      const pageLabel = element("label", "", "按原文页码查字段");
      const pageInput = element("input");
      pageInput.type = "number";
      pageInput.min = "1";
      pageInput.max = "100000";
      pageInput.required = true;
      pageInput.value = String(state.sourcePageNo);
      pageInput.setAttribute("aria-label", "查看 PDF 原文页码");
      const pageButton = element("button", "secondary-button", "打开页并查关联字段");
      pageButton.type = "submit";
      pageLabel.append(pageInput);
      pageForm.append(pageLabel, pageButton);
      pageForm.addEventListener("submit", (event) => {
        event.preventDefault();
        const pageNo = Number(pageInput.value);
        if (!Number.isInteger(pageNo) || pageNo < 1) return;
        state.sourcePageNo = pageNo;
        preview.src = `${sourceUrl}#page=${pageNo}`;
        if (!review.showSourcePage(pageNo, { scroll: true })) {
          showError("请等待业务文档修订加载后再查关联字段。");
        }
      });
      source.append(pageForm);
      const preview = element("iframe", "source-preview");
      preview.src = `${sourceUrl}#page=${state.sourcePageNo}`;
      preview.title = `${record.original_name} PDF 原文预览`;
      source.append(preview);
    } else if (kind === "image") {
      const preview = element("img", "source-preview image");
      preview.src = sourceUrl;
      preview.alt = `${record.original_name} 原图`;
      preview.addEventListener("error", () => {
        if (state.selectedId !== record.id || state.sourceStatus !== "available") return;
        state.sourceStatus = "unavailable";
        state.sourceError = "浏览器无法加载原图；原件可能已变化或图片格式无法解码。请重试核验。";
        renderDetail();
      });
      source.append(preview);
      const findFields = element("button", "secondary-button", "查找此图的关联字段");
      findFields.type = "button";
      findFields.addEventListener("click", () => {
        state.sourcePageNo = 1;
        if (!review.showSourcePage(1, { scroll: true })) showError("请等待业务文档修订加载后再查关联字段。");
      });
      source.append(findFields);
    } else {
      source.append(element("p", "preview-note", "该格式暂不支持浏览器原文预览。可下载原文件；不会伪造 PDF 页码或坐标。"));
    }
  }
  root.append(source);

  const revisions = element("section", "detail-section");
  revisions.append(element("h3", "", "解析修订"));
  if (state.revisionsError) {
    revisions.append(element("p", "error-banner", state.revisionsError));
    const retry = element("button", "secondary-button", "重试读取修订记录");
    retry.type = "button";
    retry.addEventListener("click", () => selectDocument(record.id, state.selectedSearchDocument, state.selectedReviewTarget));
    revisions.append(retry);
  } else if (state.revisions.length) {
    for (const revision of state.revisions) {
      const row = element("div", "revision-item", `${revision.tier.toUpperCase()} · 第 ${revision.page_range} 页 · MinerU ${revision.producer_version}`);
      row.append(element("small", "", new Date(revision.created_at_ms).toLocaleString("zh-CN")));
      revisions.append(row);
    }
  } else {
    revisions.append(element("p", "preview-note", task?.status === "done" ? "暂无可用修订记录。" : "解析完成后显示修订记录。"));
  }
  root.append(revisions);
}

async function selectDocument(id, searchDocument = null, reviewTarget = {}) {
  const requestNumber = ++state.selectionRequest;
  if (state.selectedId !== id) state.sourcePageNo = 1;
  state.selectedId = id;
  state.selectedSearchDocument = searchDocument;
  state.selectedReviewTarget = reviewTarget;
  state.revisions = [];
  state.revisionsError = "";
  state.sourceStatus = "checking";
  state.sourceError = "";
  review.clear();
  renderDocuments();
  renderDetail();
  void checkSource(id);
  let revisions;
  try {
    revisions = await businessApi.revisions(id);
  } catch (error) {
    if (requestNumber !== state.selectionRequest) return;
    state.revisionsError = `修订记录不可用：${error.message}`;
    renderDetail();
    return;
  }
  if (requestNumber !== state.selectionRequest) return;
  state.revisions = revisions;
  renderDetail();
  const item = state.items.find((entry) => entry.document.id === id)
    || (state.selectedSearchDocument ? { document: state.selectedSearchDocument, task: null } : null);
  if (!item) return;
  try {
    await review.setDocument(item.document, revisions, reviewTarget);
  } catch (error) {
    if (requestNumber === state.selectionRequest) showError(`复核工作台无法打开：${error.message}`);
  }
}

async function searchDocuments(event, retryQuery = null) {
  event?.preventDefault();
  const query = retryQuery ?? byId("search-query").value.trim();
  const root = byId("search-results");
  if (!query) return;
  const requestNumber = ++state.searchRequest;
  root.replaceChildren(element("p", "review-hint", "正在检索业务文档…"));
  try {
    const page = await businessApi.search(query);
    if (requestNumber !== state.searchRequest) return;
    root.replaceChildren();
    if (!page.items.length) root.append(element("p", "review-hint", "未找到已完成解析且匹配关键词的业务文档。"));
    for (const hit of page.items) {
      const row = element("div", "search-hit");
      row.append(element("strong", "", hit.document.original_name));
      row.append(element("p", "", `${hit.tier.toUpperCase()} · 当前索引预览，未人工确认：${hit.snippet}`));
      const open = element("button", "secondary-button", "打开业务文档");
      open.type = "button";
      open.addEventListener("click", () => selectDocument(hit.document.id, hit.document));
      row.append(open);
      const findPages = element("button", "secondary-button", "查找块级候选依据");
      findPages.type = "button";
      const matches = element("div", "page-matches");
      async function scan(startPage = null) {
        findPages.disabled = true;
        const loading = element("p", "review-hint", "正在扫描历史解析块（每次最多 25 页）…");
        matches.append(loading);
        try {
          const found = await businessApi.searchBlocks(hit.revision_id, query, startPage);
          if (requestNumber !== state.searchRequest) return;
          loading.remove();
          for (const match of found.items) {
            const result = element("div", "page-match");
            result.append(element("p", "", `第 ${match.page_no} 页 · 块 ${match.block_no} · 机器解析候选，未人工确认：${match.snippet}`));
            if (match.bbox) result.append(element("small", "review-hint", `模型坐标：${match.bbox.join(", ")}`));
            const read = element("button", "secondary-button", "打开并读取此块");
            read.type = "button";
            read.addEventListener("click", async () => {
              const selectionRequest = state.selectionRequest + 1;
              await selectDocument(hit.document.id, hit.document, { revisionId: hit.revision_id });
              if (selectionRequest !== state.selectionRequest || state.selectedId !== hit.document.id) return;
              await review.readHistorical(match.locator);
            });
            const freeze = element("button", "secondary-button", "冻结此块原文");
            freeze.type = "button";
            freeze.addEventListener("click", async () => {
              freeze.disabled = true;
              const selectionRequest = state.selectionRequest;
              const searchRequest = state.searchRequest;
              try {
                const evidence = await businessApi.captureEvidence(hit.revision_id, match.locator);
                if (selectionRequest !== state.selectionRequest || searchRequest !== state.searchRequest) {
                  if (result.isConnected) result.append(element("p", "review-hint", "原文已冻结到原解析修订；当前文档保持不变。"));
                  return;
                }
                await selectDocument(hit.document.id, hit.document, {
                  revisionId: hit.revision_id, evidenceId: evidence.id,
                });
              } catch (error) {
                if (selectionRequest !== state.selectionRequest || searchRequest !== state.searchRequest) {
                  freeze.disabled = false;
                  return;
                }
                result.append(element("p", "error-banner", `证据冻结失败：${error.message}`));
                freeze.disabled = false;
              }
            });
            result.append(read, freeze);
            matches.append(result);
          }
          if (!found.items.length) matches.append(element("p", "review-hint", "本次扫描的块中没有命中。"));
          if (found.next_page !== null) {
            const more = element("button", "secondary-button", "继续扫描后续页");
            more.type = "button";
            more.addEventListener("click", () => { more.remove(); scan(found.next_page); });
            matches.append(more);
          }
        } catch (error) {
          if (requestNumber !== state.searchRequest) return;
          loading.remove();
          const limitError = ["historical_search_limit_exceeded", "historical_search_too_many_matches"]
            .includes(error.message);
          const detail = limitError
            ? "块搜索达到安全上限，结果未完整返回；可换更具体的关键词，或逐页读取原文。"
            : `历史块检索失败：${error.message}`;
          matches.append(element("p", "error-banner", detail));
          if (!limitError) {
            const retry = element("button", "secondary-button", "重试扫描这一段");
            retry.type = "button";
            retry.addEventListener("click", () => {
              retry.previousElementSibling?.remove();
              retry.remove();
              scan(startPage);
            });
            matches.append(retry);
          }
        } finally {
          findPages.disabled = false;
        }
      }
      findPages.addEventListener("click", () => { matches.replaceChildren(); scan(); });
      row.append(findPages, matches);
      root.append(row);
    }
    if (!page.scan_complete) root.append(element("p", "review-hint", "检索达到返回或扫描上限，后续匹配结果可能尚未列出。"));
  } catch (error) {
    if (requestNumber !== state.searchRequest) return;
    const retry = element("button", "secondary-button", "重试本次检索");
    retry.type = "button";
    retry.addEventListener("click", () => searchDocuments(null, query));
    root.replaceChildren(element("p", "error-banner", `检索失败：${error.message}`), retry);
  }
}

async function submitFiles(event) {
  event.preventDefault();
  if (state.uploading) return;
  const capabilities = state.capabilities;
  if (!capabilities) {
    showError("服务能力不可用，上传已暂停；请重新连接并刷新。");
    return;
  }
  const files = [...byId("files").files];
  const selectedTier = byId("upload-tier").value;
  const templateCode = byId("upload-template").value;
  const listRequest = state.listRequest;
  state.uploading = true;
  updateFileSelection();
  byId("upload-feedback").replaceChildren();
  let accepted = 0;
  for (const file of files) {
    const classification = classifyFile(file, capabilities);
    if (!classification.ok) {
      feedback(file, classification.message, "error");
      continue;
    }
    const resultNode = feedback(file, "正在上传…");
    try {
      const tier = tierForFile(file, capabilities, selectedTier);
      const result = await businessApi.upload(file, { tier, templateCode });
      resultNode.textContent = `${file.name} · 已受理 · ${taskLabel(result.task.status)}`;
      resultNode.className = "feedback-item success";
      accepted += 1;
    } catch (error) {
      resultNode.textContent = `${file.name} · ${error.message}`;
      resultNode.className = "feedback-item error";
    }
  }
  state.uploading = false;
  byId("files").value = "";
  updateFileSelection();
  if (accepted) {
    if (state.listRequest === listRequest) state.page = 0;
    await refreshDocuments();
  }
}

async function pollTasks() {
  if (state.polling || state.uploading || !state.items.length) return;
  const pending = state.items.filter((item) => ["uploaded", "submitted"].includes(item.task?.status));
  if (!pending.length) return;
  state.polling = true;
  try {
    let changed = false;
    for (const item of pending) {
      try {
        const updated = await businessApi.task(item.task.id);
        if (updated.status !== item.task.status || updated.actual_tier !== item.task.actual_tier) changed = true;
      } catch (error) {
        showError(`任务状态暂不可用：${error.message}`);
      }
    }
    if (changed) {
      await refreshDocuments();
      if (state.selectedId) await selectDocument(state.selectedId);
    }
  } finally {
    state.polling = false;
  }
}

byId("files").addEventListener("change", updateFileSelection);
byId("upload-form").addEventListener("submit", submitFiles);
byId("search-form").addEventListener("submit", searchDocuments);
byId("quality-panel").addEventListener("toggle", () => {
  if (byId("quality-panel").open) refreshQualityStats();
  else state.qualityRequest += 1;
});
byId("quality-refresh").addEventListener("click", refreshQualityStats);
byId("audit-panel").addEventListener("toggle", () => { if (byId("audit-panel").open) loadAudit(true); });
byId("audit-refresh").addEventListener("click", () => loadAudit(true));
byId("audit-more").addEventListener("click", () => loadAudit());
window.addEventListener("hashchange", openEvidenceLink);
byId("refresh").addEventListener("click", refreshWorkspace);
for (const id of ["status-filter", "template-filter"]) {
  byId(id).addEventListener("change", () => { state.page = 0; refreshDocuments(); });
}
byId("previous-page").addEventListener("click", () => { if (state.page > 0) { state.page -= 1; refreshDocuments(); } });
byId("next-page").addEventListener("click", () => {
  if ((state.page + 1) * state.limit < state.total) { state.page += 1; refreshDocuments(); }
});
setInterval(pollTasks, 4000);
setInterval(() => review.refreshActive(), 4000);
bootstrap();
