"""An offline worker must reject explicit and persisted remote parse intents."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from mineru.doclib.core.db import DatabaseManager
from mineru.doclib.services.config_svc import ConfigService
from mineru.doclib.services.parse_svc import ParseService
from mineru.doclib.types import RULE_TYPE_PARSING_RULE
from mineru.errors import InvalidRequestError


def test_offline_request_and_rule_reject_remote_before_source_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _run() -> None:
        db = DatabaseManager(str(tmp_path / "doclib.db"))
        await db.initialize()
        config = ConfigService(db)
        service = ParseService(db, None, config, str(tmp_path / "data"), parse_lock_timeout_sec=1800)
        monkeypatch.setenv("MINERU_DOCLIB_REMOTE_DISABLED", "1")
        with pytest.raises(InvalidRequestError, match="disabled") as request_error:
            await service.request_parse(str(tmp_path / "missing.pdf"), remote=True)
        assert request_error.value.code == "remote_disabled"
        assert await db.fetchall("SELECT id FROM files") == []
        with pytest.raises(InvalidRequestError, match="disabled") as rule_error:
            await config.add_rule("remote", RULE_TYPE_PARSING_RULE, "*.pdf", remote=True)
        assert rule_error.value.code == "remote_disabled"
        assert await db.fetchall("SELECT id FROM parsing_rules") == []

    asyncio.run(_run())


def test_offline_worker_fails_persisted_remote_task_before_opening_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _run() -> None:
        db = DatabaseManager(str(tmp_path / "doclib.db"))
        await db.initialize()
        await db.execute(
            "INSERT INTO docs (sha256, short_id, size_bytes, file_type, first_seen_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("a" * 64, "aaaaaaa", 1, "pdf", 1, 1),
        )
        task_id = await db.execute_insert(
            "INSERT INTO parses (sha256, tier, page_range, status, privacy, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("a" * 64, "standard", "1", "parsing", "remote", 1, 1),
        )
        service = ParseService(db, None, ConfigService(db), str(tmp_path / "data"), parse_lock_timeout_sec=1800)
        monkeypatch.setenv("MINERU_DOCLIB_REMOTE_DISABLED", "1")
        assert not await service.process_doc({
            "id": task_id, "sha256": "a" * 64, "tier": "standard", "page_range": "1", "privacy": "remote",
        })
        assert await db.fetchone("SELECT status, error_code FROM parses WHERE id=?", (task_id,)) == {
            "status": "failed", "error_code": "remote_disabled",
        }

    asyncio.run(_run())
