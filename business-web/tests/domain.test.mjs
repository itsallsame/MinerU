import assert from "node:assert/strict";
import test from "node:test";
import { classifyFile, extensionOf, sourcePreviewKind, taskFailure, tierForFile } from "../src/domain.js";

const capabilities = {
  max_upload_bytes: 100,
  parseable_extensions: ["pdf", "png", "docx", "html"],
  tiered_extensions: ["pdf", "png"],
  flash_only_extensions: ["docx", "html"],
  tiers: ["flash", "basic", "standard", "advanced"],
};

test("file validation follows server capabilities", () => {
  assert.equal(extensionOf("folder\\REPORT.PDF"), "pdf");
  assert.equal(classifyFile({ name: "x.pdf", size: 101 }, capabilities).ok, false);
  assert.equal(classifyFile({ name: "x.exe", size: 1 }, capabilities).ok, false);
  assert.equal(classifyFile({ name: "x.pdf", size: 0 }, capabilities).ok, false);
  assert.equal(classifyFile({ name: "x.pdf", size: 99 }, capabilities).tiered, true);
  assert.equal(classifyFile({ name: "x.docx", size: 99 }, capabilities).tiered, false);
  assert.equal(classifyFile({ name: "x.pdf", size: 10 }, null).ok, false);
});

test("only PDF and images receive an explicit selected tier", () => {
  assert.equal(tierForFile({ name: "x.PDF", size: 10 }, capabilities, "standard"), "standard");
  assert.equal(tierForFile({ name: "x.docx", size: 10 }, capabilities, "advanced"), undefined);
  assert.throws(() => tierForFile({ name: "x.pdf", size: 10 }, capabilities, "invalid"));
});

test("only supported browser media may preview inline", () => {
  assert.equal(sourcePreviewKind("x.pdf"), "pdf");
  assert.equal(sourcePreviewKind("x.png"), "image");
  assert.equal(sourcePreviewKind("x.html"), "download");
  assert.equal(sourcePreviewKind("x.docx"), "download");
  assert.equal(sourcePreviewKind("x.tiff"), "download");
});

test("task failures distinguish recoverable parsing from source integrity loss", () => {
  assert.equal(taskFailure("source_integrity_failed").retryable, false);
  assert.match(taskFailure("source_integrity_failed").message, /重新上传原件/);
  for (const code of ["doclib_submission_failed", "doclib_parse_failed", "parse_coverage_incomplete", "parse_batch_invalid"]) {
    assert.equal(taskFailure(code).retryable, true);
    assert.ok(taskFailure(code).message.length > 10);
  }
  assert.equal(taskFailure("other_failure").retryable, true);
});
