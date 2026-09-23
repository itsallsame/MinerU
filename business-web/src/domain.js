export function extensionOf(name) {
  const base = String(name).replaceAll("\\", "/").split("/").at(-1) || "";
  const match = /\.([^.]+)$/.exec(base);
  return match ? match[1].toLowerCase() : "";
}

export function classifyFile(file, capabilities) {
  const extension = extensionOf(file.name);
  if (!capabilities) return { ok: false, message: "服务能力尚未加载，不能提交文件。" };
  if (!capabilities.parseable_extensions.includes(extension)) {
    return { ok: false, message: `不支持 .${extension || "(无扩展名)"} 文件。` };
  }
  if (file.size < 1) return { ok: false, message: "文件为空。" };
  if (file.size > capabilities.max_upload_bytes) {
    return { ok: false, message: `超过 ${formatBytes(capabilities.max_upload_bytes)} 上传上限。` };
  }
  return {
    ok: true,
    tiered: capabilities.tiered_extensions.includes(extension),
    extension,
  };
}

export function tierForFile(file, capabilities, selectedTier) {
  const classification = classifyFile(file, capabilities);
  if (!classification.ok) throw new Error(classification.message);
  if (!classification.tiered) return undefined;
  if (!capabilities.tiers.includes(selectedTier)) throw new Error("请选择有效的 PDF/图片解析档位。");
  return selectedTier;
}

export function sourcePreviewKind(name) {
  const extension = extensionOf(name);
  if (extension === "pdf") return "pdf";
  if (["png", "jpg", "jpeg", "webp", "gif", "bmp"].includes(extension)) return "image";
  return "download";
}

export function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function taskLabel(status) {
  return {
    uploaded: "待提交",
    submitted: "解析中",
    done: "已解析",
    failed: "失败",
  }[status] || "未知状态";
}

export function taskFailure(errorCode) {
  const known = {
    source_integrity_failed: {
      message: "原文件与入库时记录不一致，不能安全重试；请重新上传原件并核查存储。",
      retryable: false,
    },
    doclib_submission_failed: {
      message: "文档处理服务未能受理任务；服务恢复后可重新提交。",
      retryable: true,
    },
    doclib_parse_failed: {
      message: "底层解析失败；可以重新提交，若持续失败请检查文档与模型运行日志。",
      retryable: true,
    },
    parse_coverage_incomplete: {
      message: "解析结果未覆盖原文全部页面；可以重新提交。",
      retryable: true,
    },
    parse_batch_invalid: {
      message: "解析批次记录不一致；可以重试，若重复出现需检查文档库状态。",
      retryable: true,
    },
  };
  return known[errorCode] || {
    message: "任务失败；可重试。若再次失败，请记录下面的代码并检查服务日志。",
    retryable: true,
  };
}
