"""MHTML 归档从业务后缀到同步/异步解析的回归。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from mineru.backend.analyze import aio_doc_analyze, doc_analyze
from mineru.parser import parse


MHTML_SAMPLE = b"""MIME-Version: 1.0\r
Content-Type: multipart/related; boundary=archive; type=\"text/html\"\r
Subject: Saved report\r
\r
--archive\r
Content-Type: text/html; charset=utf-8\r
Content-Location: https://example.invalid/report.html\r
\r
<!doctype html><html><head><title>Saved report</title></head><body>\r
<h1>Archive heading</h1><p>Offline content.</p></body></html>\r
--archive--\r
"""


def test_mhtml_doc_analyze_uses_native_archive_route() -> None:
    middle, model = doc_analyze(MHTML_SAMPLE, effort="xhigh", file_suffix="mhtml")
    async_middle, async_model = asyncio.run(aio_doc_analyze(MHTML_SAMPLE, file_suffix="mhtml"))

    assert middle.model_dump() == async_middle.model_dump()
    assert model.pages == async_model.pages
    assert middle.metadata.file_suffix == model.metadata.file_suffix == "mhtml"
    assert middle.metadata.document.title == "Saved report"
    assert middle.extensions["mineru"] == {"tier": "flash", "parse_mode": "txt"}
    assert "Archive heading" in str(middle.pages)
    assert "Offline content." in str(middle.pages)


def test_mhtml_and_mht_paths_parse_as_flash(tmp_path: Path) -> None:
    for suffix in ("mhtml", "mht"):
        source = tmp_path / f"sample.{suffix}"
        source.write_bytes(MHTML_SAMPLE)

        result = parse(source, tier="flash")

        assert result.middle_json.metadata.file_suffix == "mhtml"
        assert "Archive heading" in result.markdown()
