# Copyright (c) Opendatalab. All rights reserved.
"""MHTML 归档 model-list 生产入口。"""

from __future__ import annotations

from docvortex import analyze as analyze_native
from docvortex.document.contracts import HtmlSourceContext

from .contracts import AnalysisResult


def analyze_mhtml(
    file_bytes: bytes,
    *,
    source_context: HtmlSourceContext | None = None,
) -> AnalysisResult:
    """复用 DocVortex 的受限归档解析，不把 MHTML 误送 Office 转换器。"""
    native = analyze_native(file_bytes, file_suffix="mhtml", source_context=source_context)
    return AnalysisResult(
        model_list=native.model_json.pages,
        effort="flash",
        parse_mode="txt",
        elapsed=native.elapsed_seconds,
    )


__all__ = ["analyze_mhtml"]
