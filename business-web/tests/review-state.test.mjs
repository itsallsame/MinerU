import assert from "node:assert/strict";
import test from "node:test";
import { canNavigateEvidence, confirmationBlockers, confirmedResultMarkdown, evidenceHighlightParts, latestDecisions } from "../src/review-state.js";

const template = { fields: [{ code: "title", label: "标题", required: true }, { code: "issuer", label: "发文单位", required: false }] };
const run = { run: { status: "done" }, issues: [] };
const decision = { id: "decision-1", field_code: "title", value: "通知", evidence_id: "evidence-1" };

test("machine candidates never satisfy required review decisions", () => {
  assert.deepEqual(confirmationBlockers({ ...run, candidates: [{ field_code: "title", value: "通知" }] }, template, [], []), [
    "必填字段「标题」尚未作出复核决定。",
  ]);
  assert.deepEqual(confirmationBlockers(run, template, [decision], []), []);
  assert.deepEqual(confirmationBlockers({ ...run, issues: [{ status: "open" }] }, template, [decision], []), [
    "仍有未处理的问题。",
  ]);
  assert.deepEqual(confirmationBlockers({ run: { status: "queued" }, issues: [] }, template, [decision], []), [
    "字段提取尚未完成。",
  ]);
});

test("latest decisions and immutable versions prevent unchanged reconfirmation", () => {
  const later = { ...decision, id: "decision-2", value: "新通知" };
  assert.equal(latestDecisions([decision, later]).get("title"), later);
  const prior = [{ fields: [{ field_code: "title", decision_id: "decision-1" }] }];
  assert.equal(confirmationBlockers(run, template, [decision], prior).length, 1);
  assert.deepEqual(confirmationBlockers(run, template, [decision, later], prior), []);
});

test("only verified current content may offer a page jump", () => {
  assert.equal(canNavigateEvidence({ navigation_status: "current_match", page_no: 2 }), true);
  assert.equal(canNavigateEvidence({ navigation_status: "changed", page_no: 2 }), false);
  assert.equal(canNavigateEvidence({ navigation_status: "unavailable", page_no: 2 }), false);
});

test("frozen evidence highlights only literal matches and caps repeated marks", () => {
  assert.deepEqual(evidenceHighlightParts("标题：年度通知", "年度通知"), [
    { text: "标题：", match: false }, { text: "年度通知", match: true },
  ]);
  assert.deepEqual(evidenceHighlightParts("标题：年度通知", "规范化通知"), [
    { text: "标题：年度通知", match: false },
  ]);
  const parts = evidenceHighlightParts("a".repeat(100), "a");
  assert.equal(parts.filter((part) => part.match).length, 20);
  assert.equal(parts.map((part) => part.text).join(""), "a".repeat(100));
});

test("Markdown export contains only confirmed fields and evidence identities", () => {
  const markdown = confirmedResultMarkdown({ version: 2, revision_id: "rev", fields: [
    { field_code: "title", value: "通知", evidence_id: "evidence-1" },
  ] }, template);
  assert.match(markdown, /确认成果 v2/);
  assert.match(markdown, /标题：通知/);
  assert.match(markdown, /evidence-1/);
  assert.doesNotMatch(markdown, /候选/);
});
