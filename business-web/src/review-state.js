export function latestDecisions(decisions) {
  const latest = new Map();
  for (const decision of decisions) latest.set(decision.field_code, decision);
  return latest;
}

export function confirmationBlockers(extraction, template, decisions, results) {
  if (!extraction || extraction.run.status !== "done") return ["字段提取尚未完成。"];
  const latest = latestDecisions(decisions);
  const blockers = [];
  if (extraction.issues.some((issue) => issue.status === "open")) blockers.push("仍有未处理的问题。");
  for (const field of template?.fields || []) {
    if (field.required && !latest.has(field.code)) blockers.push(`必填字段「${field.label}」尚未作出复核决定。`);
  }
  if (!blockers.length && results.length) {
    const prior = results[0].fields;
    const same = prior.length === latest.size && prior.every((field) => {
      const decision = latest.get(field.field_code);
      return decision && decision.id === field.decision_id;
    });
    if (same) blockers.push("与上一版成果相比，没有新的字段复核决定。");
  }
  return blockers;
}

export function canNavigateEvidence(evidence) {
  return evidence?.navigation_status === "current_match" && Number.isInteger(evidence.page_no) && evidence.page_no > 0;
}

export function confirmedResultMarkdown(result, template) {
  const labels = new Map((template?.fields || []).map((field) => [field.code, field.label]));
  const lines = [`# 确认成果 v${result.version}`, "", `解析修订：${result.revision_id}`, ""];
  for (const field of result.fields) {
    const label = labels.get(field.field_code) || field.field_code;
    lines.push(`- ${label}：${field.value.replaceAll("\n", "\n  ")}`);
    lines.push(`  - 证据 ID：${field.evidence_id}`);
  }
  return `${lines.join("\n")}\n`;
}
