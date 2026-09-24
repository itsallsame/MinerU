"""One consumer's force retry creates at most one batch per submission generation."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from mineru.doclib.core.db import DatabaseManager
from mineru.doclib.core.fts import FTSManager
from mineru.doclib.services import parse_svc as parse_svc_module
from mineru.doclib.services.parse_svc import ParseService
from mineru.errors import InvalidRequestError


class _NoRules:
    async def match_rules(self, path: str, rule_type: str) -> list[dict[str, Any]]:
        return []


async def _metadata(path: str) -> dict[str, Any]:
    return {"page_count": 1, "title": None, "author": None, "subject": None,
            "keywords": None, "is_image_based": 0}


async def _service(tmp_path: Path) -> tuple[DatabaseManager, ParseService]:
    db = DatabaseManager(str(tmp_path / "doclib.db"))
    await db.initialize()
    service = ParseService(
        db=db, fts=FTSManager(db), config_svc=_NoRules(),
        data_dir=str(tmp_path / "data"), parse_lock_timeout_sec=1800,
    )
    return db, service


def test_force_attempt_replays_first_response_after_batch_finished(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(parse_svc_module, "extract_metadata", _metadata)

    async def _run() -> None:
        db, service = await _service(tmp_path)
        source = tmp_path / "retry.pdf"
        source.write_bytes(b"%PDF-1.7\nretry")
        first = await service.request_parse(
            str(source), tier="standard", force=True, consumer_key="business:task", submission_attempt=1,
        )
        first_id = first.created_parse_ids[0]
        await db.execute("UPDATE parses SET status='done' WHERE id=?", (first_id,))

        replay = await service.request_parse(
            str(source), tier="standard", force=True, consumer_key="business:task", submission_attempt=1,
        )
        assert replay == first
        assert await db.fetchone("SELECT COUNT(*) AS count FROM parses") == {"count": 1}

        second = await service.request_parse(
            str(source), tier="standard", force=True, consumer_key="business:task", submission_attempt=2,
        )
        assert second.created_parse_ids and second.created_parse_ids != first.created_parse_ids
        assert await service.request_parse(
            str(source), tier="standard", force=True, consumer_key="business:task", submission_attempt=2,
        ) == second
        assert await db.fetchone("SELECT COUNT(*) AS count FROM parses") == {"count": 2}
        assert await db.fetchone("SELECT latest_attempt FROM parse_intents WHERE consumer_key='business:task'") == {
            "latest_attempt": 2
        }
        assert await db.fetchone("SELECT COUNT(*) AS count FROM parse_submissions") == {"count": 2}
        assert await service.request_parse(
            str(source), tier="standard", force=True, consumer_key="business:task", submission_attempt=1,
        ) == first

        reopened_db = DatabaseManager(db.db_path)
        await reopened_db.initialize()
        reopened = ParseService(
            db=reopened_db, fts=FTSManager(reopened_db), config_svc=_NoRules(),
            data_dir=str(tmp_path / "data"), parse_lock_timeout_sec=1800,
        )
        assert await reopened.request_parse(
            str(source), tier="standard", force=True, consumer_key="business:task", submission_attempt=2,
        ) == second

    asyncio.run(_run())


def test_attempt_rejects_changed_request_gap_and_late_submit_after_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(parse_svc_module, "extract_metadata", _metadata)

    async def _run() -> None:
        _db, service = await _service(tmp_path)
        source = tmp_path / "identity.pdf"
        source.write_bytes(b"%PDF-1.7\nidentity")
        with pytest.raises(InvalidRequestError) as missing_key:
            await service.request_parse(str(source), tier="standard", submission_attempt=1)
        assert missing_key.value.code == "submission_attempt_invalid"
        with pytest.raises(InvalidRequestError) as gap:
            await service.request_parse(
                str(source), tier="standard", consumer_key="business:identity", submission_attempt=2,
            )
        assert gap.value.code == "submission_attempt_gap"
        first = await service.request_parse(
            str(source), tier="standard", consumer_key="business:identity", submission_attempt=1,
        )
        with pytest.raises(InvalidRequestError) as conflict:
            await service.request_parse(
                str(source), tier="basic", consumer_key="business:identity", submission_attempt=1,
            )
        assert conflict.value.code == "submission_attempt_conflict"
        assert await service.request_parse(
            str(source), tier="standard", consumer_key="business:identity", submission_attempt=1,
        ) == first
        with pytest.raises(InvalidRequestError) as unversioned:
            await service.request_parse(str(source), tier="standard", consumer_key="business:identity")
        assert unversioned.value.code == "submission_attempt_required"
        await service.release_consumer("business:identity")
        with pytest.raises(InvalidRequestError) as released:
            await service.request_parse(
                str(source), tier="standard", consumer_key="business:identity", submission_attempt=2,
            )
        assert released.value.code == "consumer_released"

    asyncio.run(_run())


def test_concurrent_same_attempt_creates_one_batch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parse_svc_module, "extract_metadata", _metadata)

    async def _run() -> None:
        db, first_service = await _service(tmp_path)
        other_db = DatabaseManager(db.db_path)
        await other_db.initialize()
        second_service = ParseService(
            db=other_db, fts=FTSManager(other_db), config_svc=_NoRules(),
            data_dir=str(tmp_path / "data"), parse_lock_timeout_sec=1800,
        )
        source = tmp_path / "concurrent.pdf"
        source.write_bytes(b"%PDF-1.7\nconcurrent")
        first, second = await asyncio.gather(
            first_service.request_parse(
                str(source), tier="standard", force=True, consumer_key="business:race", submission_attempt=1,
            ),
            second_service.request_parse(
                str(source), tier="standard", force=True, consumer_key="business:race", submission_attempt=1,
            ),
        )
        assert first == second
        assert await db.fetchone("SELECT COUNT(*) AS count FROM parses") == {"count": 1}

    asyncio.run(_run())
