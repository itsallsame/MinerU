"""New parse batches must never overwrite older revision JSON."""

from __future__ import annotations

import json
from pathlib import Path

from mineru.doclib.services.parse_svc import write_unique_parse_batch_json


def test_same_millisecond_batch_writes_keep_both_payloads(tmp_path: Path) -> None:
    first_ms = write_unique_parse_batch_json(str(tmp_path), "1-2", {"source": "first"}, start_ms=12345)
    second_ms = write_unique_parse_batch_json(str(tmp_path), "1-2", {"source": "second"}, start_ms=12345)

    assert first_ms == 12345
    assert second_ms == 12346
    assert json.loads((tmp_path / "1-2_12345.json").read_text()) == {"source": "first"}
    assert json.loads((tmp_path / "1-2_12346.json").read_text()) == {"source": "second"}


__all__ = []
