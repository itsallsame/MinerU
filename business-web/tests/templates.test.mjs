import assert from "node:assert/strict";
import test from "node:test";
import { validateTemplateDraft } from "../src/templates.js";

test("custom template fields retain order and explicit type and required status", () => {
  const draft = validateTemplateDraft({
    code: "my_report", name: " 报告 ", fields: [
      { code: "title", label: " 标题 ", type: "text", required: true },
      { code: "written_date", label: "日期", type: "date", required: false },
    ],
  });
  assert.equal(draft.name, "报告");
  assert.deepEqual(draft.fields.map((field) => field.code), ["title", "written_date"]);
  assert.deepEqual(draft.fields.map((field) => field.type), ["text", "date"]);
  assert.deepEqual(draft.fields.map((field) => field.required), [true, false]);
});

test("invalid or duplicate field definitions are rejected before writing", () => {
  const field = { code: "title", label: "标题", type: "text", required: true };
  assert.throws(() => validateTemplateDraft({ code: "Bad-Code", name: "报告", fields: [field] }));
  assert.throws(() => validateTemplateDraft({ code: "report", name: "报告", fields: [field, field] }));
  assert.throws(() => validateTemplateDraft({ code: "report", name: "报告", fields: [] }));
  assert.throws(() => validateTemplateDraft({ code: "report", name: "报告", fields: [{ ...field, type: "number" }] }));
});
