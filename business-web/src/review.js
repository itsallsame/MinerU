import { businessApi } from "./api.js";
import { canNavigateEvidence, confirmationBlockers, confirmedResultMarkdown, latestDecisions } from "./review-state.js";

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
    inspection: null, reading: null, error: "", busy: false, generation: 0,
  };
  const currentRevision = () => state.revisions.find((item) => item.id === state.revisionId);

  function clear() {
    state.generation += 1;
    state.document = null;
    state.revisions = [];
    state.revisionId = null;
    state.runId = null;
    state.extraction = null;
    state.inspection = null;
    state.reading = null;
    root.replaceChildren();
    root.hidden = true;
  }

  async function perform(action) {
    if (state.busy) return;
    state.busy = true;
    state.error = "";
    render();
    try {
      await action();
    } catch (error) {
      state.error = error.message;
    } finally {
      state.busy = false;
      render();
    }
  }

  async function loadRun(runId) {
    const generation = ++state.generation;
    state.runId = runId;
    state.extraction = null;
    state.inspection = null;
    render();
    try {
      const extraction = await businessApi.extraction(runId);
      const [template, decisions, results, audit] = await Promise.all([
        businessApi.templateVersion(extraction.run.template_code, extraction.run.template_version),
        businessApi.decisions(runId), businessApi.results(runId), businessApi.audit(runId),
      ]);
      if (generation !== state.generation) return;
      state.extraction = extraction;
      state.runs = state.runs.map((run) => run.id === runId ? extraction.run : run);
      state.template = template;
      state.decisions = decisions;
      state.results = results;
      state.audit = audit;
      state.error = "";
      render();
    } catch (error) {
      if (generation !== state.generation) return;
      state.error = `复核数据读取失败：${error.message}`;
      render();
    }
  }

  async function selectRevision(revisionId) {
    const generation = ++state.generation;
    state.revisionId = revisionId;
    state.runId = null;
    state.extraction = null;
    state.inspection = null;
    state.reading = null;
    state.runs = [];
    state.template = null;
    state.decisions = [];
    state.results = [];
    state.audit = [];
    state.evidence = [];
    render();
    try {
      const [runs, evidence] = await Promise.all([
        businessApi.extractions(revisionId), businessApi.revisionEvidence(revisionId),
      ]);
      if (generation !== state.generation) return;
      state.runs = runs;
      state.evidence = evidence;
      if (runs.length) {
        await loadRun(runs[0].id);
      } else {
        state.error = "";
        render();
      }
    } catch (error) {
      if (generation !== state.generation) return;
      state.error = `解析修订读取失败：${error.message}`;
      render();
    }
  }

  async function setDocument(documentRecord, revisions, { revisionId = null, evidenceId = null } = {}) {
    state.generation += 1;
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
    state.reading = null;
    state.error = "";
    root.hidden = false;
    render();
    if (revisionId && !revisions.some((revision) => revision.id === revisionId)) {
      throw new Error("证据对应的解析修订已不存在，无法打开证据链接。");
    }
    if (revisions.length) await selectRevision(revisionId || revisions[0].id);
    if (evidenceId) {
      if (!state.evidence.some((evidence) => evidence.id === evidenceId)) {
        throw new Error("证据不属于当前解析修订，无法打开证据链接。");
      }
      await inspectEvidence(evidenceId);
    }
  }

  async function startExtraction() {
    await perform(async () => {
      const revisionId = state.revisionId;
      const created = await businessApi.enqueueExtraction(revisionId);
      if (revisionId !== state.revisionId) return;
      const [runs, evidence] = await Promise.all([
        businessApi.extractions(revisionId), businessApi.revisionEvidence(revisionId),
      ]);
      if (revisionId !== state.revisionId) return;
      state.runs = runs;
      state.evidence = evidence;
      await loadRun(created.id);
    });
  }

  async function refreshActive() {
    if (!state.runId || !["queued", "running"].includes(state.extraction?.run.status) || state.busy) return;
    await loadRun(state.runId);
    if (state.extraction?.run.status === "done") {
      try {
        state.evidence = await businessApi.revisionEvidence(state.revisionId);
        render();
      } catch (error) {
        state.error = `证据列表读取失败：${error.message}`;
        render();
      }
    }
  }

  async function inspectEvidence(evidenceId) {
    await perform(async () => {
      state.inspection = await businessApi.inspectEvidence(evidenceId);
    });
    if (state.inspection?.id === evidenceId) {
      root.querySelector(".evidence-inspection")?.scrollIntoView({ block: "center" });
    }
  }

  async function readHistorical(locator) {
    await perform(async () => {
      const revisionId = state.revisionId;
      const reading = await businessApi.readRevision(revisionId, locator);
      if (revisionId === state.revisionId) state.reading = reading;
    });
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
    page.value = "1";
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
      readHistorical(`doc:${state.document.sha256.slice(0, 12)}/tier:${revision.tier}/page:${pageNo}`);
    });
    box.append(form);
    box.append(element("p", "review-hint", "读取指定解析修订的历史内容；这是机器解析文本，未人工确认，也不是冻结证据。"));
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
    page.value = "1";
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
      const locator = `doc:${state.document.sha256.slice(0, 12)}/tier:${revision.tier}/page:${pageNumber}`;
      perform(async () => {
        const created = await businessApi.captureEvidence(revision.id, locator);
        state.evidence = await businessApi.revisionEvidence(revision.id);
        state.inspection = await businessApi.inspectEvidence(created.id);
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
      card.append(element("pre", "evidence-snippet", info.snippet));
      const link = element("a", "evidence-link", "在新页面打开此证据 ↗");
      link.href = `#evidence=${encodeURIComponent(info.id)}`;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      card.append(link);
      if (canNavigateEvidence(info)) {
        const jump = button("尝试跳转当前原文页 ↗", () => {
          if (!onEvidenceNavigate(info)) {
            state.error = "该原文格式不支持可靠的浏览器页码跳转。";
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
      const card = element("div", "field-review");
      const heading = element("div", "field-heading");
      heading.append(element("strong", "", field.label));
      if (field.required) heading.append(element("span", "required-tag", "必填"));
      card.append(heading);
      const chosen = latest.get(field.code);
      if (chosen) card.append(element("p", "review-decision", `已复核：${chosen.value} · ${chosen.basis === "candidate_acceptance" ? "接受候选" : "人工修订"}`));
      const candidates = state.extraction.candidates.filter((item) => item.field_code === field.code);
      if (!candidates.length) card.append(element("p", "review-hint", "暂无机器候选；可在采集证据后人工填写。"));
      for (const candidate of candidates) {
        const row = element("div", "candidate-row");
        row.append(element("span", "", candidate.value));
        row.append(button("看证据", () => inspectEvidence(candidate.evidence_id), state.busy));
        row.append(button("接受候选", () => perform(async () => {
          await businessApi.decideField(state.runId, field.code, candidate.value, candidate.evidence_id);
          await loadRun(state.runId);
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
        perform(async () => {
          await businessApi.decideField(state.runId, field.code, value.value, evidence.value, reason.value || null);
          await loadRun(state.runId);
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
            perform(async () => {
              await businessApi.resolveIssue(issue.id, "ignored", reason.value);
              await loadRun(state.runId);
            });
          });
          form.append(ignore);
        }
        form.addEventListener("submit", (event) => {
          event.preventDefault();
          perform(async () => {
            await businessApi.resolveIssue(issue.id, "resolved", reason.value);
            await loadRun(state.runId);
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
    const blockers = confirmationBlockers(state.extraction, state.template, state.decisions, state.results);
    if (blockers.length) {
      const list = element("ul", "blockers");
      for (const reason of blockers) list.append(element("li", "", reason));
      box.append(list);
    }
    box.append(button("确认并生成不可变成果版本", () => perform(async () => {
      await businessApi.confirm(state.runId);
      await loadRun(state.runId);
    }), state.busy || !!blockers.length));
    if (!state.results.length) box.append(element("p", "review-hint", "尚无已确认成果。机器候选不会自动进入成果。"));
    for (const result of state.results) {
      const card = element("div", "result-card");
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
    const tools = element("div", "review-tools");
    const revisionSelect = element("select");
    revisionSelect.setAttribute("aria-label", "选择解析修订");
    for (const revision of state.revisions) {
      const option = element("option", "", `${revision.tier.toUpperCase()} · ${new Date(revision.created_at_ms).toLocaleString("zh-CN")}`);
      option.value = revision.id;
      revisionSelect.append(option);
    }
    revisionSelect.value = state.revisionId;
    revisionSelect.addEventListener("change", () => selectRevision(revisionSelect.value));
    tools.append(revisionSelect);
    if (state.runs.length) {
      const runSelect = element("select");
      runSelect.setAttribute("aria-label", "选择提取运行");
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
      state.busy || !state.document.template_code || ["queued", "running"].includes(state.extraction?.run.status),
    ));
    root.append(tools);
    root.append(renderReading(), renderEvidence());
    if (!state.document.template_code) {
      root.append(element("p", "review-hint", "此文档上传时未绑定业务模板；不能在当前修订上执行模板字段提取。"));
      return;
    }
    if (!state.runId) {
      root.append(element("p", "review-hint", "选择“生成字段候选”后，后台将按上传时冻结的模板版本执行提取。"));
      return;
    }
    if (!state.extraction) {
      root.append(element("p", "review-hint", "正在读取提取运行…"));
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

  return { clear, setDocument, refreshActive };
}
