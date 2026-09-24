import { businessApi } from "./api.js";
import { canNavigateEvidence, confirmationBlockers, confirmedResultMarkdown, evidenceHighlightParts, latestDecisions } from "./review-state.js";

const issueLabels = {
  required_missing: "必填字段缺失",
  conflicting_candidates: "多个候选值冲突",
  coverage_incomplete: "原文解析覆盖不完整",
};
const runLabels = { queued: "等待提取", running: "正在提取", done: "待人工复核", failed: "提取失败" };

function element(tag, className = "", content = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content) node.textContent = content;
  return node;
}

function button(label, handler, disabled = false) {
  const node = element("button", "secondary-button", label);
  node.type = "button";
  node.disabled = disabled;
  node.addEventListener("click", handler);
  return node;
}

function section(title) {
  const node = element("section", "review-section");
  node.append(element("h4", "", title));
  return node;
}

function downloadResult(result, template, format) {
  const contents = format === "json"
    ? `${JSON.stringify(result, null, 2)}\n`
    : confirmedResultMarkdown(result, template);
  const blob = new Blob([contents], { type: format === "json" ? "application/json" : "text/markdown" });
  const url = URL.createObjectURL(blob);
  const link = element("a");
  link.href = url;
  link.download = `confirmed-result-v${result.version}.${format === "json" ? "json" : "md"}`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function createReviewWorkbench(root, { onEvidenceNavigate = () => false } = {}) {
  const state = {
    document: null, revisions: [], revisionId: null, runs: [], runId: null,
    extraction: null, template: null, evidence: [], decisions: [], results: [], audit: [],
    inspection: null, reading: null, outline: null, structure: null, error: "", busy: false, generation: 0,
    diff: null, diffOtherId: null, diffPreview: null,
    selectedFieldCode: null, inspectionHighlightValue: null,
    sourcePageNo: null,
    revisionLoading: false, runLoading: false,
    revisionLoadFailed: false, runLoadFailed: false, evidenceLoadFailed: false,
    evidenceLoading: false, targetRunId: null, contextVersion: 0,
  };
  const currentRevision = () => state.revisions.find((item) => item.id === state.revisionId);

  function clear() {
    state.generation += 1;
    state.contextVersion += 1;
    state.document = null;
    state.revisions = [];
    state.revisionId = null;
    state.runId = null;
    state.extraction = null;
    state.inspection = null;
    state.reading = null;
    state.outline = null;
    state.structure = null;
    state.diff = null;
    state.diffOtherId = null;
    state.diffPreview = null;
    state.selectedFieldCode = null;
    state.inspectionHighlightValue = null;
    state.sourcePageNo = null;
    state.revisionLoading = false;
    state.runLoading = false;
    state.revisionLoadFailed = false;
    state.runLoadFailed = false;
    state.evidenceLoadFailed = false;
    state.evidenceLoading = false;
    state.targetRunId = null;
    state.busy = false;
    state.error = "";
    root.replaceChildren();
    root.hidden = true;
  }

  async function perform(action) {
    if (state.busy) return;
    const contextVersion = state.contextVersion;
    const isCurrent = () => contextVersion === state.contextVersion;
    const active = document.activeElement;
    const focusedFieldCode = root.contains(active) ? active.closest(".field-review")?.dataset.fieldCode : null;
    const focusedIssueId = root.contains(active) ? active.closest(".issue-card")?.dataset.issueId : null;
    const focusedConfirmation = root.contains(active)
      && active.textContent === "确认并生成不可变成果版本";
    const restoreFocus = () => {
      if (!isCurrent()) return;
      const card = focusedFieldCode
        ? [...root.querySelectorAll(".field-review")].find((node) => node.dataset.fieldCode === focusedFieldCode)
        : focusedIssueId
          ? [...root.querySelectorAll(".issue-card")].find((node) => node.dataset.issueId === focusedIssueId)
          : focusedConfirmation
            ? root.querySelector(".result-card") || root.querySelector(".review-section:last-of-type")
            : null;
      card?.focus({ preventScroll: true });
    };
    state.busy = true;
    state.error = "";
    render();
    restoreFocus();
    try {
      await action(isCurrent);
    } catch (error) {
      if (isCurrent()) state.error = error.message;
    } finally {
      if (isCurrent()) {
        state.busy = false;
        render();
        restoreFocus();
      }
    }
  }

  async function loadRun(runId) {
    const generation = ++state.generation;
    state.runId = runId;
    state.extraction = null;
    state.runLoading = true;
    state.runLoadFailed = false;
    state.error = "";
    state.inspection = null;
    state.selectedFieldCode = null;
    state.inspectionHighlightValue = null;
    render();
    try {
      const extraction = await businessApi.extraction(runId);
      const [template, decisions, results, audit] = await Promise.all([
        businessApi.templateVersion(extraction.run.template_code, extraction.run.template_version),
        businessApi.decisions(runId), businessApi.results(runId), businessApi.audit(runId),
      ]);
      if (generation !== state.generation) return;
      state.extraction = extraction;
      state.runLoading = false;
      state.runs = state.runs.map((run) => run.id === runId ? extraction.run : run);
      state.template = template;
      state.decisions = decisions;
      state.results = results;
      state.audit = audit;
      state.error = "";
      render();
    } catch (error) {
      if (generation !== state.generation) return;
      state.runLoading = false;
      state.runLoadFailed = true;
      state.error = `复核数据读取失败：${error.message}`;
      render();
    }
  }

  async function refreshAfterReviewWrite(runId, isCurrent, acknowledgement) {
    if (!isCurrent()) return;
    await loadRun(runId);
    if (isCurrent() && state.runLoadFailed) {
      state.error = `${acknowledgement}，但${state.error}`;
    }
  }

  async function selectRevision(revisionId, targetRunId = null) {
    const generation = ++state.generation;
    state.contextVersion += 1;
    state.busy = false;
    state.revisionId = revisionId;
    state.targetRunId = targetRunId;
    state.runId = null;
    state.extraction = null;
    state.inspection = null;
    state.selectedFieldCode = null;
    state.inspectionHighlightValue = null;
    state.reading = null;
    state.outline = null;
    state.structure = null;
    state.diff = null;
    state.diffOtherId = null;
    state.diffPreview = null;
    state.runs = [];
    state.template = null;
    state.decisions = [];
    state.results = [];
    state.audit = [];
    state.evidence = [];
    state.revisionLoading = true;
    state.revisionLoadFailed = false;
    state.runLoading = false;
    state.runLoadFailed = false;
    state.evidenceLoadFailed = false;
    state.evidenceLoading = false;
    state.error = "";
    render();
    try {
      const [runs, evidence] = await Promise.all([
        businessApi.extractions(revisionId), businessApi.revisionEvidence(revisionId),
      ]);
      if (generation !== state.generation) return;
      state.runs = runs;
      state.evidence = evidence;
      state.revisionLoading = false;
      if (targetRunId && !runs.some((run) => run.id === targetRunId)) {
        throw new Error("审计记录对应的提取运行不属于此解析修订。");
      }
      if (runs.length) {
        await loadRun(targetRunId || runs[0].id);
      } else {
        state.error = "";
        render();
      }
    } catch (error) {
      if (generation !== state.generation) return;
      state.revisionLoading = false;
      state.revisionLoadFailed = true;
      state.error = `解析修订读取失败：${error.message}`;
      render();
    }
  }

  async function setDocument(documentRecord, revisions, { revisionId = null, evidenceId = null, runId = null } = {}) {
    state.generation += 1;
    state.contextVersion += 1;
    state.busy = false;
    state.document = documentRecord;
    state.revisions = revisions;
    state.revisionId = null;
    state.runId = null;
    state.extraction = null;
    state.template = null;
    state.runs = [];
    state.evidence = [];
    state.decisions = [];
    state.results = [];
    state.audit = [];
    state.inspection = null;
    state.selectedFieldCode = null;
    state.inspectionHighlightValue = null;
    state.sourcePageNo = null;
    state.revisionLoading = false;
    state.runLoading = false;
    state.revisionLoadFailed = false;
    state.runLoadFailed = false;
    state.evidenceLoadFailed = false;
    state.evidenceLoading = false;
    state.targetRunId = null;
    state.reading = null;
    state.outline = null;
    state.structure = null;
    state.diff = null;
    state.diffOtherId = null;
    state.diffPreview = null;
    state.error = "";
    root.hidden = false;
    render();
    if (revisionId && !revisions.some((revision) => revision.id === revisionId)) {
      throw new Error("证据对应的解析修订已不存在，无法打开证据链接。");
    }
    if (revisions.length) await selectRevision(revisionId || revisions[0].id, runId);
    if (evidenceId) {
      if (!state.evidence.some((evidence) => evidence.id === evidenceId)) {
        throw new Error("证据不属于当前解析修订，无法打开证据链接。");
      }
      await inspectEvidence(evidenceId);
    }
  }

  async function startExtraction() {
    await perform(async (isCurrent) => {
      const revisionId = state.revisionId;
      const created = await businessApi.enqueueExtraction(revisionId);
      if (!isCurrent()) return;
      state.runId = created.id;
      state.targetRunId = created.id;
      state.runs = [created, ...state.runs.filter((run) => run.id !== created.id)];
      state.extraction = null;
      try {
        const [runs, evidence] = await Promise.all([
          businessApi.extractions(revisionId), businessApi.revisionEvidence(revisionId),
        ]);
        if (!isCurrent()) return;
        state.runs = runs.some((run) => run.id === created.id) ? runs : [created, ...runs];
        state.evidence = evidence;
      } catch (error) {
        if (!isCurrent()) return;
        state.revisionLoadFailed = true;
        state.error = `字段提取已提交，但解析修订读取失败：${error.message}`;
        return;
      }
      await loadRun(created.id);
      if (isCurrent() && state.runLoadFailed) {
        state.error = `字段提取已提交，但${state.error}`;
      }
    });
  }

  async function refreshEvidence() {
    if (state.evidenceLoading || !state.revisionId) return;
    const revisionId = state.revisionId;
    const runId = state.runId;
    const contextVersion = state.contextVersion;
    state.evidenceLoading = true;
    render();
    try {
      const evidence = await businessApi.revisionEvidence(revisionId);
      if (contextVersion !== state.contextVersion || runId !== state.runId) return;
      state.evidence = evidence;
      state.evidenceLoadFailed = false;
      state.error = "";
    } catch (error) {
      if (contextVersion !== state.contextVersion || runId !== state.runId) return;
      state.evidenceLoadFailed = true;
      state.error = `证据列表读取失败：${error.message}`;
    } finally {
      if (contextVersion === state.contextVersion && runId === state.runId) {
        state.evidenceLoading = false;
        render();
      }
    }
  }

  async function refreshActive() {
    if (!state.runId || !["queued", "running"].includes(state.extraction?.run.status) || state.busy) return;
    const runId = state.runId;
    await loadRun(runId);
    if (state.runId === runId && state.extraction?.run.status === "done") {
      await refreshEvidence();
    }
  }

  async function inspectEvidence(evidenceId, fieldCode = null, value = null) {
    let inspected = false;
    await perform(async (isCurrent) => {
      const inspection = await businessApi.inspectEvidence(evidenceId);
      if (!isCurrent()) return;
      state.selectedFieldCode = fieldCode;
      state.inspectionHighlightValue = value;
      state.inspection = inspection;
      inspected = true;
    });
    if (inspected) {
      root.querySelector(".evidence-inspection")?.scrollIntoView({ block: "center" });
    }
    return inspected;
  }

  function focusField(fieldCode, value = null) {
    state.selectedFieldCode = fieldCode;
    state.inspectionHighlightValue = value;
    render();
    const card = [...root.querySelectorAll(".field-review")].find((node) => node.dataset.fieldCode === fieldCode);
    card?.focus({ preventScroll: true });
    card?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function linkedFieldsForEvidence(evidenceId) {
    const linked = new Map();
    for (const candidate of state.extraction?.candidates || []) {
      if (candidate.evidence_id === evidenceId) linked.set(candidate.field_code, {
        value: candidate.value, kind: "机器候选",
      });
    }
    for (const decision of latestDecisions(state.decisions).values()) {
      if (decision.evidence_id === evidenceId) linked.set(decision.field_code, {
        value: decision.value, kind: "已复核决定",
      });
    }
    return linked;
  }

  function showSourcePage(pageNo, { scroll = false } = {}) {
    if (!state.document || !state.revisionId || !Number.isInteger(pageNo) || pageNo < 1) return false;
    state.sourcePageNo = pageNo;
    render();
    if (scroll) root.querySelector(".source-field-index")?.scrollIntoView({ behavior: "smooth", block: "start" });
    return true;
  }

  function renderSourceFieldIndex() {
    if (state.sourcePageNo === null) return null;
    const box = section(`原文第 ${state.sourcePageNo} 页 → 字段`);
    box.classList.add("source-field-index");
    box.append(element("p", "review-hint", "按原文页码查看当前解析修订已冻结的证据及其字段关联；只显示已冻结范围，不代表该页全部内容或所有字段。"));
    if (state.revisionLoading) {
      box.append(element("p", "review-hint", "正在读取当前解析修订的冻结证据…"));
      return box;
    }
    if (state.error.startsWith("解析修订读取失败")) {
      box.append(element("p", "error-banner", "此修订证据尚不可用，不能判断该页是否有关联字段。"));
      return box;
    }
    const evidenceOnPage = state.evidence.filter((item) => item.page_no === state.sourcePageNo);
    if (!evidenceOnPage.length) {
      box.append(element("p", "review-hint", "此修订在该页尚无冻结证据；不能据此判断原文没有相关字段。"));
    }
    for (const evidence of evidenceOnPage) {
      const row = element("div", "source-field-row");
      row.append(element("p", "", `冻结证据 · ${evidence.block_no ? `块 ${evidence.block_no}` : "页级"} · ${evidence.snippet.slice(0, 120)}`));
      row.append(button("核验此冻结证据", () => inspectEvidence(evidence.id), state.busy));
      const linked = linkedFieldsForEvidence(evidence.id);
      if (state.runLoading) row.append(element("p", "review-hint", "正在读取当前提取运行的字段关联…"));
      else if (state.runs.length && !state.extraction) row.append(element("p", "error-banner", "提取运行未能加载，字段关联状态未知。"));
      else if (["queued", "running"].includes(state.extraction?.run.status)) row.append(element("p", "review-hint", "字段提取尚未完成，当前没有可复核的关联字段。"));
      else if (state.extraction?.run.status === "failed") row.append(element("p", "error-banner", "字段提取失败；不展示可能不完整的候选关联。"));
      else if (!linked.size) row.append(element("p", "review-hint", "此证据在当前提取运行中尚无关联字段。"));
      for (const [fieldCode, relation] of linked) {
        const label = state.template?.fields.find((field) => field.code === fieldCode)?.label || fieldCode;
        row.append(button(`定位字段：${label} · ${relation.kind}`, async () => {
          if (await inspectEvidence(evidence.id, fieldCode, relation.value)) focusField(fieldCode, relation.value);
        }, state.busy));
      }
      box.append(row);
    }
    return box;
  }

  async function readHistorical(locator) {
    await perform(async (isCurrent) => {
      const revisionId = state.revisionId;
      const reading = await businessApi.readRevision(revisionId, locator);
      if (isCurrent()) state.reading = reading;
    });
  }

  async function compareRevisions(otherId, startPage = null) {
    await perform(async (isCurrent) => {
      const revisionId = state.revisionId;
      let page;
      try {
        page = await businessApi.diffRevisions(revisionId, otherId, startPage);
      } catch (error) {
        if (!isCurrent() || otherId !== state.diffOtherId) return;
        throw error;
      }
      if (!isCurrent() || otherId !== state.diffOtherId) return;
      state.diff = {
        items: startPage === null ? page.items : [...(state.diff?.items || []), ...page.items],
        nextPage: page.next_page,
        scannedPages: (startPage === null ? 0 : state.diff?.scannedPages || 0) + page.scanned_pages,
      };
      state.diffPreview = null;
    });
  }

  async function readDiffPage(item) {
    await perform(async (isCurrent) => {
      const leftRevisionId = state.revisionId;
      const rightRevisionId = state.diffOtherId;
      let left;
      let right;
      try {
        [left, right] = await Promise.all([
          item.left_locator ? businessApi.readRevision(leftRevisionId, item.left_locator, 30000) : null,
          item.right_locator ? businessApi.readRevision(rightRevisionId, item.right_locator, 30000) : null,
        ]);
      } catch (error) {
        if (!isCurrent() || rightRevisionId !== state.diffOtherId) return;
        throw error;
      }
      if (!isCurrent() || rightRevisionId !== state.diffOtherId) return;
      if (left?.truncated || right?.truncated) throw new Error("页面过长，无法完整展示两版文本；不能据此判断全部差异。");
      state.diffPreview = { pageNo: item.page_no, left: left?.content ?? null, right: right?.content ?? null };
    });
  }

  function renderRevisionDiff() {
    if (state.revisions.length < 2) return null;
    const box = section("解析修订逐页差异");
    box.append(element("p", "review-hint", "只比较同一业务文档两次历史解析的完整页文本。机器解析结果未人工确认；相同文本不代表结构或字段成果相同。每次最多扫描 25 页。"));
    const controls = element("div", "review-tools");
    const other = element("select");
    other.setAttribute("aria-label", "对比的解析修订");
    for (const revision of state.revisions.filter((item) => item.id !== state.revisionId)) {
      const option = element("option", "", `${revision.tier.toUpperCase()} · 第 ${revision.page_range} 页 · ${new Date(revision.created_at_ms).toLocaleString("zh-CN")}`);
      option.value = revision.id;
      other.append(option);
    }
    if (!state.diffOtherId || ![...other.options].some((option) => option.value === state.diffOtherId)) {
      state.diffOtherId = other.options[0].value;
    }
    other.value = state.diffOtherId;
    other.addEventListener("change", () => {
      state.diffOtherId = other.value;
      state.diff = null;
      state.diffPreview = null;
      render();
    });
    controls.append(other, button("比较修订", () => compareRevisions(other.value), state.busy));
    box.append(controls);
    if (!state.diff) return box;
    const labels = { same: "页文本相同", changed: "页文本不同", only_left: "仅当前修订有此页", only_right: "仅对比修订有此页" };
    for (const item of state.diff.items) {
      const row = element("div", "diff-page-row");
      row.append(element("span", "", `第 ${item.page_no} 页 · ${labels[item.status] || item.status}`));
      row.append(button("读取两版此页", () => readDiffPage(item), state.busy));
      box.append(row);
    }
    box.append(element("p", "review-hint", `已扫描 ${state.diff.scannedPages} 页${state.diff.nextPage === null ? " · 已到末页" : " · 后续仍有页面"}。`));
    if (state.diff.nextPage !== null) {
      box.append(button("继续比较后续页", () => compareRevisions(state.diffOtherId, state.diff.nextPage), state.busy));
    }
    if (state.diffPreview) {
      const preview = element("div", "diff-preview");
      for (const [label, content] of [["当前修订", state.diffPreview.left], ["对比修订", state.diffPreview.right]]) {
        const column = element("div");
        column.append(element("strong", "", `${label} · 第 ${state.diffPreview.pageNo} 页`));
        column.append(element("pre", "", content ?? "此修订没有该页。"));
        preview.append(column);
      }
      box.append(preview);
    }
    return box;
  }

  async function loadOutline(startPage = null) {
    await perform(async (isCurrent) => {
      const revisionId = state.revisionId;
      const page = await businessApi.outline(revisionId, startPage);
      if (!isCurrent()) return;
      state.outline = {
        items: startPage === null ? page.items : [...(state.outline?.items || []), ...page.items],
        nextPage: page.next_page,
        scannedPages: (startPage === null ? 0 : state.outline?.scannedPages || 0) + page.scanned_pages,
      };
    });
  }

  async function loadStructure(pageNo) {
    await perform(async (isCurrent) => {
      const revisionId = state.revisionId;
      const page = await businessApi.structure(revisionId, pageNo);
      if (isCurrent()) state.structure = page;
    });
  }

  function renderOutline() {
    const box = section("解析标题目录");
    box.append(element("p", "review-hint", "根据历史解析 Markdown 标题生成，机器结果未人工确认；仅支持页级定位，每次最多扫描 25 页。"));
    if (!state.outline) {
      box.append(button("读取标题目录", () => loadOutline(), state.busy));
      return box;
    }
    if (!state.outline.items.length) box.append(element("p", "review-hint", "已扫描页面尚未识别标题。"));
    for (const heading of state.outline.items) {
      const row = element("div", "outline-item");
      row.style.marginLeft = `${Math.min(heading.level - 1, 5) * 18}px`;
      row.append(element("span", "", `${heading.title} · 第 ${heading.page_no} 页`));
      row.append(button("读取所在页", () => readHistorical(heading.locator), state.busy));
      row.append(button("查看本页块", () => loadStructure(heading.page_no), state.busy));
      box.append(row);
    }
    box.append(element("p", "review-hint", `已扫描 ${state.outline.scannedPages} 页${state.outline.nextPage === null ? " · 已到末页" : " · 后续仍有页面"}。`));
    if (state.outline.nextPage !== null) {
      box.append(button("继续扫描目录", () => loadOutline(state.outline.nextPage), state.busy));
    }
    return box;
  }

  function renderStructure() {
    const box = section("原生解析块结构");
    box.append(element("p", "review-hint", "按历史解析批次读取原生父子块树；子节点沿用顶层块定位器，不代表子节点精确定位。机器结果未人工确认。"));
    const form = element("form", "read-form");
    const label = element("label", "", "结构页码");
    const input = element("input");
    input.type = "number";
    input.min = "1";
    input.value = String(state.structure?.page_no || Number.parseInt(currentRevision()?.page_range || "1", 10) || 1);
    input.required = true;
    input.setAttribute("aria-label", "原生结构页码");
    label.append(input);
    const submit = element("button", "secondary-button", "查看本页块");
    submit.type = "submit";
    submit.disabled = state.busy;
    form.append(label, submit);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const pageNo = Number(input.value);
      if (Number.isInteger(pageNo) && pageNo > 0) loadStructure(pageNo);
    });
    box.append(form);
    if (!state.structure) return box;
    box.append(element("p", "review-hint", `第 ${state.structure.page_no} 页 · ${state.structure.blocks.length} 个顶层块`));
    if (!state.structure.blocks.length) box.append(element("p", "review-hint", "此页没有可列出的顶层块。"));
    function appendBlock(block, depth = 0) {
      const row = element("div", "structure-block");
      row.style.marginLeft = `${Math.min(depth, 8) * 18}px`;
      const labelText = `${block.type}${block.level ? ` · 标题级别 ${block.level}` : ""}${block.block_no ? ` · 块 ${block.block_no}` : " · 页级定位"}${depth ? " · 父块定位" : ""}`;
      row.append(element("strong", "", labelText));
      if (block.preview) row.append(element("span", "", block.preview));
      if (block.bbox) row.append(element("small", "", `坐标：${block.bbox.join(", ")}`));
      if (!depth) row.append(button("读取此块", () => readHistorical(block.locator), state.busy));
      box.append(row);
      for (const child of block.children || []) appendBlock(child, depth + 1);
    }
    for (const block of state.structure.blocks) appendBlock(block);
    return box;
  }

  function renderReading() {
    const box = section("历史解析渐进读取");
    const revision = currentRevision();
    const form = element("form", "read-form");
    const label = element("label", "", "解析页码");
    const page = element("input");
    page.type = "number";
    page.min = "1";
    page.max = "100000";
    page.value = revision ? String(Number.parseInt(revision.page_range, 10) || 1) : "1";
    page.required = true;
    page.setAttribute("aria-label", "历史解析页码");
    const start = element("button", "secondary-button", "读取这一页");
    start.type = "submit";
    start.disabled = state.busy || !revision;
    label.append(page);
    form.append(label, start);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const pageNo = Number(page.value);
      if (!revision || !Number.isInteger(pageNo) || pageNo < 1) return;
      readHistorical(`doc:${revision.short_id}/tier:${revision.tier}/page:${pageNo}`);
    });
    box.append(form);
    box.append(element("p", "review-hint", `解析页范围：${revision?.page_range || "未知"}。这是机器解析文本，未人工确认，也不是冻结证据。`));
    if (state.reading) {
      box.append(element("p", "review-hint", `当前定位器：${state.reading.locator}`));
      box.append(element("pre", "historical-content", state.reading.content || "该位置没有可读取的文本。"));
      if (state.reading.next_locator) {
        box.append(button("继续读取下一段", () => readHistorical(state.reading.next_locator), state.busy));
      } else if (state.reading.truncated) {
        box.append(element("p", "review-hint", "内容已截断，但服务未提供可续读定位器；请缩小读取范围。"));
      }
    }
    return box;
  }

  function renderEvidence() {
    const box = section("冻结证据与原文核验");
    const revision = currentRevision();
    const picker = element("div", "review-tools");
    const select = element("select");
    select.setAttribute("aria-label", "选择已有证据");
    select.append(element("option", "", "选择已有证据"));
    select.firstElementChild.value = "";
    for (const evidence of state.evidence) {
      const option = element("option", "", `第 ${evidence.page_no} 页 · ${evidence.snippet.slice(0, 38)}`);
      option.value = evidence.id;
      select.append(option);
    }
    select.addEventListener("change", () => { if (select.value) inspectEvidence(select.value); });
    picker.append(select);
    box.append(picker);

    const capture = element("form", "capture-form");
    const label = element("label", "", "按页采集历史解析证据");
    const page = element("input");
    page.type = "number";
    page.min = "1";
    page.max = "100000";
    page.value = revision ? String(Number.parseInt(revision.page_range, 10) || 1) : "1";
    page.required = true;
    page.setAttribute("aria-label", "原文页码");
    const captureButton = element("button", "secondary-button", "采集第 N 页");
    captureButton.type = "submit";
    captureButton.disabled = state.busy;
    label.append(page);
    capture.append(label, captureButton);
    capture.addEventListener("submit", (event) => {
      event.preventDefault();
      const pageNumber = Number(page.value);
      if (!Number.isInteger(pageNumber) || pageNumber < 1 || !revision) return;
      const locator = `doc:${revision.short_id}/tier:${revision.tier}/page:${pageNumber}`;
      perform(async (isCurrent) => {
        const created = await businessApi.captureEvidence(revision.id, locator);
        if (!isCurrent()) return;
        state.evidence = [created, ...state.evidence.filter((item) => item.id !== created.id)];
        state.inspection = null;
        try {
          const evidence = await businessApi.revisionEvidence(revision.id);
          if (!isCurrent()) return;
          state.evidence = evidence.some((item) => item.id === created.id) ? evidence : [created, ...evidence];
        } catch (error) {
          if (!isCurrent()) return;
          state.evidenceLoadFailed = true;
          state.error = `证据已冻结，但证据列表读取失败：${error.message}`;
          return;
        }
        try {
          const inspection = await businessApi.inspectEvidence(created.id);
          if (isCurrent()) state.inspection = inspection;
        } catch (error) {
          if (isCurrent()) state.error = `证据已冻结，但证据核验读取失败：${error.message}`;
        }
      });
    });
    box.append(capture);
    box.append(element("p", "review-hint", "采集会从该解析修订的历史批次读取并冻结片段；并非由浏览器提交任意文本。"));

    if (state.inspection) {
      const info = state.inspection;
      const status = {
        current_match: "当前定位内容与冻结片段一致；不等于证明仍是同一历史解析批次。",
        changed: "当前定位内容已变化；冻结片段仍可查看，但不能可靠跳回历史位置。",
        unavailable: "当前定位不可用；冻结片段仍可查看，但不能跳回原文。",
      }[info.navigation_status];
      const card = element("div", "evidence-inspection");
      card.append(element("p", "review-hint", `第 ${info.page_no} 页 · ${status}`));
      const snippet = element("pre", "evidence-snippet");
      const parts = evidenceHighlightParts(info.snippet, state.inspectionHighlightValue);
      for (const part of parts) snippet.append(element(part.match ? "mark" : "span", "", part.text));
      card.append(snippet);
      if (state.inspectionHighlightValue && !parts.some((part) => part.match)) {
        card.append(element("p", "review-hint", "该复核值不在冻结片段中逐字出现；保留证据关联，但不伪造原文高亮。"));
      }
      const linked = linkedFieldsForEvidence(info.id);
      if (linked.size) {
        card.append(element("p", "review-hint", "此冻结证据关联的字段："));
        const links = element("div", "evidence-field-links");
        for (const [fieldCode, relation] of linked) {
          const label = state.template?.fields.find((field) => field.code === fieldCode)?.label || fieldCode;
          links.append(button(`${label} · ${relation.kind}`, () => focusField(fieldCode, relation.value)));
        }
        card.append(links);
      }
      const link = element("a", "evidence-link", "在新页面打开此证据 ↗");
      link.href = `#evidence=${encodeURIComponent(info.id)}`;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      card.append(link);
      if (canNavigateEvidence(info)) {
        const jump = button("尝试跳转当前原文页 ↗", () => {
          if (!onEvidenceNavigate(info)) {
            state.error = "原文当前不可预览，或该格式不支持可靠的浏览器页码跳转。";
            render();
          }
        });
        card.append(jump);
      }
      box.append(card);
    }
    return box;
  }

  function renderFields() {
    const box = section("字段候选与人工决定");
    box.append(element("p", "draft-banner", "机器候选 · 未确认。只有明确复核决定才会进入成果。"));
    const latest = latestDecisions(state.decisions);
    for (const field of state.template.fields) {
      const card = element("div", `field-review ${state.selectedFieldCode === field.code ? "active" : ""}`);
      card.dataset.fieldCode = field.code;
      card.tabIndex = -1;
      const heading = element("div", "field-heading");
      heading.append(element("strong", "", field.label));
      if (field.required) heading.append(element("span", "required-tag", "必填"));
      card.append(heading);
      const chosen = latest.get(field.code);
      if (chosen) {
        card.append(element("p", "review-decision", `已复核：${chosen.value} · ${chosen.basis === "candidate_acceptance" ? "接受候选" : "人工修订"}`));
        card.append(button("查看已复核字段的证据", () => inspectEvidence(chosen.evidence_id, field.code, chosen.value), state.busy));
      }
      const candidates = state.extraction.candidates.filter((item) => item.field_code === field.code);
      if (!candidates.length) card.append(element("p", "review-hint", "暂无机器候选；可在采集证据后人工填写。"));
      for (const candidate of candidates) {
        const row = element("div", "candidate-row");
        row.append(element("span", "", candidate.value));
        row.append(button("看证据", () => inspectEvidence(candidate.evidence_id, field.code, candidate.value), state.busy));
        row.append(button("接受候选", () => perform(async (isCurrent) => {
          const runId = state.runId;
          await businessApi.decideField(runId, field.code, candidate.value, candidate.evidence_id);
          await refreshAfterReviewWrite(runId, isCurrent, "复核决定已保存");
        }), state.busy));
        card.append(row);
      }
      const form = element("form", "field-edit-form");
      const value = element("textarea");
      value.rows = 2;
      value.maxLength = 2000;
      value.required = true;
      value.value = chosen?.value || "";
      value.placeholder = "人工填写或修订字段值";
      value.setAttribute("aria-label", `${field.label}的复核值`);
      const evidence = element("select");
      evidence.required = true;
      evidence.setAttribute("aria-label", `${field.label}的证据`);
      const placeholder = element("option", "", "选择原文证据");
      placeholder.value = "";
      evidence.append(placeholder);
      for (const item of state.evidence) {
        const option = element("option", "", `第 ${item.page_no} 页 · ${item.snippet.slice(0, 25)}`);
        option.value = item.id;
        evidence.append(option);
      }
      if (chosen) evidence.value = chosen.evidence_id;
      const reason = element("input");
      reason.type = "text";
      reason.maxLength = 2000;
      reason.placeholder = "修订原因（人工修改时必填）";
      reason.setAttribute("aria-label", `${field.label}的修订原因`);
      const save = element("button", "secondary-button", "保存复核决定");
      save.type = "submit";
      save.disabled = state.busy || !state.evidence.length;
      form.append(value, evidence, reason, save);
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        perform(async (isCurrent) => {
          const runId = state.runId;
          await businessApi.decideField(runId, field.code, value.value, evidence.value, reason.value || null);
          await refreshAfterReviewWrite(runId, isCurrent, "复核决定已保存");
        });
      });
      card.append(form);
      box.append(card);
    }
    return box;
  }

  function renderIssues() {
    const box = section("问题队列");
    const latest = latestDecisions(state.decisions);
    if (!state.extraction.issues.length) box.append(element("p", "review-hint", "未发现规则问题；仍需对必填字段作出显式复核决定。"));
    for (const issue of state.extraction.issues) {
      const field = state.template.fields.find((item) => item.code === issue.field_code);
      const card = element("div", `issue-card ${issue.status === "open" ? "open" : "closed"}`);
      card.dataset.issueId = issue.id;
      card.tabIndex = -1;
      const label = issueLabels[issue.code] || issue.code;
      card.append(element("strong", "", `${field?.label || "文档整体"} · ${label}`));
      card.append(element("span", "issue-state", `${issue.severity === "blocking" ? "必核" : "提示"} · ${issue.status === "open" ? "未处理" : "已处理"}`));
      if (issue.status === "open" && issue.code !== "coverage_incomplete") {
        const form = element("form", "issue-form");
        const reason = element("input");
        reason.type = "text";
        reason.required = true;
        reason.maxLength = 2000;
        reason.placeholder = "填写处理依据或原因";
        reason.setAttribute("aria-label", `${label}处理原因`);
        const resolve = button("标记已解决", () => {}, state.busy || !latest.has(issue.field_code));
        resolve.type = "submit";
        if (!latest.has(issue.field_code)) resolve.title = "须先完成对应字段的复核决定";
        form.append(reason, resolve);
        if (issue.code !== "required_missing") {
          const ignore = button("有依据地忽略", () => {}, state.busy);
          ignore.addEventListener("click", () => {
            if (!reason.reportValidity()) return;
            perform(async (isCurrent) => {
              const runId = state.runId;
              await businessApi.resolveIssue(issue.id, "ignored", reason.value);
              await refreshAfterReviewWrite(runId, isCurrent, "问题处理已保存");
            });
          });
          form.append(ignore);
        }
        form.addEventListener("submit", (event) => {
          event.preventDefault();
          perform(async (isCurrent) => {
            const runId = state.runId;
            await businessApi.resolveIssue(issue.id, "resolved", reason.value);
            await refreshAfterReviewWrite(runId, isCurrent, "问题处理已保存");
          });
        });
        card.append(form);
      } else if (issue.code === "coverage_incomplete" && issue.status === "open") {
        card.append(element("p", "review-hint", "原文覆盖不完整不能人工豁免；需重新解析得到完整修订。"));
      }
      box.append(card);
    }
    return box;
  }

  function renderResults() {
    const box = section("确认成果与版本");
    box.tabIndex = -1;
    const blockers = confirmationBlockers(state.extraction, state.template, state.decisions, state.results);
    if (blockers.length) {
      const list = element("ul", "blockers");
      for (const reason of blockers) list.append(element("li", "", reason));
      box.append(list);
    }
    box.append(button("确认并生成不可变成果版本", () => perform(async (isCurrent) => {
      const runId = state.runId;
      const confirmed = await businessApi.confirm(runId);
      await refreshAfterReviewWrite(runId, isCurrent, `成果 v${confirmed.version} 已确认`);
    }), state.busy || !!blockers.length));
    if (!state.results.length) box.append(element("p", "review-hint", "尚无已确认成果。机器候选不会自动进入成果。"));
    for (const result of state.results) {
      const card = element("div", "result-card");
      card.tabIndex = -1;
      card.append(element("strong", "", `确认成果 v${result.version}`));
      card.append(element("small", "", new Date(result.created_at_ms).toLocaleString("zh-CN")));
      for (const field of result.fields) {
        const name = state.template.fields.find((item) => item.code === field.field_code)?.label || field.field_code;
        card.append(element("p", "", `${name}：${field.value}`));
      }
      const actions = element("div", "result-actions");
      actions.append(button("下载 JSON", () => downloadResult(result, state.template, "json")));
      actions.append(button("下载 Markdown", () => downloadResult(result, state.template, "markdown")));
      card.append(actions);
      box.append(card);
    }
    return box;
  }

  function render() {
    root.replaceChildren();
    if (!state.document) return;
    const header = element("div", "review-heading");
    const title = element("div");
    title.append(element("span", "eyebrow", "REVIEW WORKBENCH"), element("h3", "", "证据与复核"));
    header.append(title);
    if (state.extraction) header.append(element("span", "review-state", runLabels[state.extraction.run.status] || state.extraction.run.status));
    root.append(header);
    if (state.error) root.append(element("p", "error-banner", state.error));
    if (!state.revisions.length) {
      root.append(element("p", "review-hint", "文档尚无完成的解析修订；请等待任务完成后再提取字段。"));
      return;
    }
    if (state.revisionLoadFailed) {
      root.append(button("重试读取解析修订", () => selectRevision(state.revisionId, state.targetRunId)));
      return;
    }
    const tools = element("div", "review-tools");
    const revisionSelect = element("select");
    revisionSelect.setAttribute("aria-label", "选择解析修订");
    revisionSelect.disabled = state.busy;
    for (const revision of state.revisions) {
      const option = element("option", "", `${revision.tier.toUpperCase()} · 第 ${revision.page_range} 页 · ${new Date(revision.created_at_ms).toLocaleString("zh-CN")}`);
      option.value = revision.id;
      revisionSelect.append(option);
    }
    revisionSelect.value = state.revisionId;
    revisionSelect.addEventListener("change", () => selectRevision(revisionSelect.value));
    tools.append(revisionSelect);
    if (state.runs.length) {
      const runSelect = element("select");
      runSelect.setAttribute("aria-label", "选择提取运行");
      runSelect.disabled = state.busy;
      for (const run of state.runs) {
        const option = element("option", "", `${runLabels[run.status] || run.status} · ${new Date(run.created_at_ms).toLocaleString("zh-CN")}`);
        option.value = run.id;
        runSelect.append(option);
      }
      runSelect.value = state.runId;
      runSelect.addEventListener("change", () => loadRun(runSelect.value));
      tools.append(runSelect);
    }
    tools.append(button(
      state.runs.length ? "重新生成字段候选" : "生成字段候选",
      startExtraction,
      state.busy || state.revisionLoading || state.runLoading || state.runLoadFailed
        || !state.document.template_code || ["queued", "running"].includes(state.extraction?.run.status),
    ));
    root.append(tools);
    if (state.evidenceLoadFailed || state.evidenceLoading) {
      root.append(element("p", "review-hint", "冻结证据列表尚未成功读取；证据及字段关联状态未知。"));
      if (state.evidenceLoadFailed) {
        root.append(button("重试读取证据列表", refreshEvidence, state.evidenceLoading));
      }
      return;
    }
    const sourceLinks = renderSourceFieldIndex();
    const diff = renderRevisionDiff();
    root.append(...(sourceLinks ? [sourceLinks] : []), ...(diff ? [diff] : []), renderOutline(), renderStructure(), renderReading(), renderEvidence());
    if (!state.document.template_code) {
      root.append(element("p", "review-hint", "此文档上传时未绑定业务模板；不能在当前修订上执行模板字段提取。"));
      return;
    }
    if (!state.runId) {
      root.append(element("p", "review-hint", "选择“生成字段候选”后，后台将按上传时冻结的模板版本执行提取。"));
      return;
    }
    if (!state.extraction) {
      if (state.runLoadFailed) root.append(button("重试读取提取运行", () => loadRun(state.runId)));
      else root.append(element("p", "review-hint", "正在读取提取运行…"));
      return;
    }
    if (["queued", "running"].includes(state.extraction.run.status)) {
      root.append(element("p", "review-hint", "字段提取正在后台执行；尚未形成可复核的候选。"));
      return;
    }
    if (state.extraction.run.status === "failed") {
      root.append(element("p", "error-banner", `字段提取失败：${state.extraction.run.error_code || "未知原因"}。失败运行不展示部分候选。`));
      return;
    }
    if (!state.template) return;
    root.append(renderFields(), renderIssues(), renderResults());
    if (state.audit.length) {
      const details = element("details", "audit-details");
      details.append(element("summary", "", `操作记录 · ${state.audit.length} 条（只记录来源，不代表个人身份）`));
      for (const event of state.audit) {
        details.append(element("p", "", `${new Date(event.created_at_ms).toLocaleString("zh-CN")} · ${event.source} · ${event.action}`));
      }
      root.append(details);
    }
  }

  return { clear, setDocument, refreshActive, readHistorical, showSourcePage };
}
