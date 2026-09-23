"""Versioned business extraction contracts, independent of MinerU parsing tiers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

FieldType = Literal["text", "date", "list"]
_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


@dataclass(frozen=True)
class TemplateField:
    code: str
    label: str
    type: FieldType = "text"
    required: bool = False


@dataclass(frozen=True)
class TemplateVersion:
    code: str
    name: str
    version: int
    built_in: bool
    enabled: bool
    fields: tuple[TemplateField, ...]
    created_at_ms: int


def validate_template(code: str, name: str, fields: tuple[TemplateField, ...]) -> None:
    if not _CODE.fullmatch(code):
        raise ValueError("Template code must be lowercase ASCII letters, digits or underscores")
    if not name.strip() or len(name) > 128:
        raise ValueError("Template name must be 1-128 characters")
    if not fields or len(fields) > 100:
        raise ValueError("Template must have 1-100 fields")
    seen: set[str] = set()
    for field in fields:
        if not _CODE.fullmatch(field.code) or field.code in seen:
            raise ValueError("Template field codes must be unique lowercase ASCII identifiers")
        if not field.label.strip() or len(field.label) > 128:
            raise ValueError("Template field label must be 1-128 characters")
        if field.type not in ("text", "date", "list"):
            raise ValueError("Unsupported template field type")
        seen.add(field.code)


BUILTIN_TEMPLATES: tuple[tuple[str, str, tuple[TemplateField, ...]], ...] = (
    ("official_document", "公文", (
        TemplateField("title", "标题", required=True),
        TemplateField("document_number", "文号"),
        TemplateField("issuer", "发文单位"),
        TemplateField("written_date", "成文日期", "date"),
        TemplateField("main_recipient", "主送单位"),
        TemplateField("keywords", "主题词", "list"),
    )),
    ("paper", "论文", (
        TemplateField("title", "题目", required=True),
        TemplateField("authors", "作者", "list"),
        TemplateField("organization", "单位"),
        TemplateField("abstract", "摘要"),
        TemplateField("keywords", "关键词", "list"),
        TemplateField("references", "参考文献", "list"),
    )),
    ("research_report", "研究报告", (
        TemplateField("title", "报告名称", required=True),
        TemplateField("organization", "编制单位"),
        TemplateField("owner", "负责人"),
        TemplateField("report_date", "日期", "date"),
        TemplateField("abstract", "摘要"),
        TemplateField("conclusion", "结论"),
    )),
    ("newspaper", "报纸", (
        TemplateField("newspaper_name", "报纸名称", required=True),
        TemplateField("issue_date", "日期", "date"),
        TemplateField("page_number", "版次"),
        TemplateField("column", "栏目"),
        TemplateField("article_title", "文章标题", required=True),
        TemplateField("authors", "作者", "list"),
        TemplateField("body", "正文"),
    )),
)


__all__ = ["BUILTIN_TEMPLATES", "FieldType", "TemplateField", "TemplateVersion", "validate_template"]
