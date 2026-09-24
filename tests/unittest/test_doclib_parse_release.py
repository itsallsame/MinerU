"""Truthful queued-only release of shared Doclib parse work."""

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


class _NoRulesConfig:
    async def match_rules(self, path: str, rule_type: str) -> list[dict[str, Any]]:
        return []


async def _metadata(path: str) -> dict[str, Any]:
    return {
        "page_count": 1,
        "title": None,
        "author": None,
        "subject": None,
        "keywords": None,
        "is_image_based": 0,
    }


async def _service(tmp_path: Path) -> tuple[DatabaseManager, ParseService]:
    db = DatabaseManager(str(tmp_path / "doclib.db"))
    await db.initialize()
    return db, ParseService(
        db=db,
        fts=FTSManager(db),
        config_svc=_NoRulesConfig(),
        data_dir=str(tmp_path / "data"),
        parse_lock_timeout_sec=1800,
    )


def test_exclusive_pending_release_skips_queue_and_tombstones_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parse_svc_module, "extract_metadata", _metadata)

    async def _run() -> None:
        db, service = await _service(tmp_path)
        source = tmp_path / "exclusive.pdf"
        source.write_bytes(b"%PDF-1.7\nexclusive")
        response = await service.request_parse(str(source), tier="standard", consumer_key="business:one")
        parse_id = response.wait_parse_ids[0]

        first = await service.release_consumer("business:one")
        assert [(item.parse_id, item.disposition, item.status_at_release) for item in first.results] == [
            (parse_id, "skipped", "skipped")
        ]
        assert await service.get_queue_length() == 0
        assert await service.acquire_task() is None
        assert await service.release_consumer("business:one") == first
        await db.execute("DELETE FROM parses WHERE id=?", (parse_id,))
        assert await db.fetchone("SELECT COUNT(*) AS count FROM parse_consumers") == {"count": 0}
        assert await service.release_consumer("business:one") == first
        reopened_db = DatabaseManager(db.db_path)
        await reopened_db.initialize()
        reopened = ParseService(
            db=reopened_db,
            fts=FTSManager(reopened_db),
            config_svc=_NoRulesConfig(),
            data_dir=str(tmp_path / "data"),
            parse_lock_timeout_sec=1800,
        )
        assert await reopened.release_consumer("business:one") == first
        assert await db.fetchone("SELECT state FROM parse_intents WHERE consumer_key='business:one'") == {"state": "released"}
        with pytest.raises(InvalidRequestError) as error:
            await service.request_parse(str(source), tier="standard", consumer_key="business:one", force=True)
        assert error.value.code == "consumer_released"
        replacement = await service.request_parse(str(source), tier="standard", consumer_key="business:new")
        assert replacement.created_parse_ids and replacement.created_parse_ids != [parse_id]

    asyncio.run(_run())


def test_shared_release_waits_for_last_consumer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parse_svc_module, "extract_metadata", _metadata)

    async def _run() -> None:
        db, service = await _service(tmp_path)
        source = tmp_path / "shared.pdf"
        source.write_bytes(b"%PDF-1.7\nshared")
        first = await service.request_parse(str(source), tier="standard", consumer_key="business:first")
        second = await service.request_parse(str(source), tier="standard", consumer_key="business:second")
        assert first.wait_parse_ids == second.wait_parse_ids
        parse_id = first.wait_parse_ids[0]

        first_release = await service.release_consumer("business:first")
        assert first_release.results[0].disposition == "shared"
        assert await db.fetchone("SELECT status FROM parses WHERE id=?", (parse_id,)) == {"status": "pending"}
        second_release = await service.release_consumer("business:second")
        assert second_release.results[0].disposition == "skipped"
        assert await service.release_consumer("business:first") == first_release

    asyncio.run(_run())


def test_one_intent_releases_every_partial_page_batch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _five_page_metadata(path: str) -> dict[str, Any]:
        return {**await _metadata(path), "page_count": 5}

    monkeypatch.setattr(parse_svc_module, "extract_metadata", _five_page_metadata)

    async def _run() -> None:
        db, service = await _service(tmp_path)
        source = tmp_path / "pages.pdf"
        source.write_bytes(b"%PDF-1.7\npages")
        first = await service.request_parse(str(source), tier="standard", page_range="1-2", consumer_key="business:pages")
        second = await service.request_parse(str(source), tier="standard", page_range="4-5", consumer_key="business:pages")
        assert first.created_parse_ids and second.created_parse_ids
        released = await service.release_consumer("business:pages")
        assert {item.parse_id for item in released.results} == {first.created_parse_ids[0], second.created_parse_ids[0]}
        assert {item.disposition for item in released.results} == {"skipped"}
        assert await db.fetchone("SELECT COUNT(*) AS count FROM parses WHERE status='pending'") == {"count": 0}

    asyncio.run(_run())


def test_background_duplicate_keeps_business_release_shared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parse_svc_module, "extract_metadata", _metadata)

    async def _run() -> None:
        db, service = await _service(tmp_path)
        source = tmp_path / "business.pdf"
        duplicate = tmp_path / "background.pdf"
        source.write_bytes(b"%PDF-1.7\nduplicate")
        duplicate.write_bytes(source.read_bytes())
        response = await service.request_parse(str(source), tier="standard", consumer_key="business:one")
        await service.ingest_file(str(duplicate), trigger="background")
        released = await service.release_consumer("business:one")
        assert released.results[0].disposition == "shared"
        assert await db.fetchone("SELECT status FROM parses WHERE id=?", (response.wait_parse_ids[0],)) == {"status": "pending"}

    asyncio.run(_run())


def test_anonymous_sdk_request_protects_shared_batch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parse_svc_module, "extract_metadata", _metadata)

    async def _run() -> None:
        db, service = await _service(tmp_path)
        source = tmp_path / "sdk.pdf"
        source.write_bytes(b"%PDF-1.7\nsdk")
        business = await service.request_parse(str(source), tier="standard", consumer_key="business:sdk")
        anonymous = await service.request_parse(str(source), tier="standard")
        assert anonymous.wait_parse_ids == business.wait_parse_ids
        released = await service.release_consumer("business:sdk")
        assert released.results[0].disposition == "shared"
        assert await db.fetchone("SELECT status FROM parses WHERE id=?", (business.wait_parse_ids[0],)) == {"status": "pending"}
        assert await db.fetchone(
            "SELECT protected FROM parse_consumers WHERE parse_id=? AND consumer_key='system:request'",
            (business.wait_parse_ids[0],),
        ) == {"protected": 1}

    asyncio.run(_run())


def test_running_work_is_not_reported_as_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parse_svc_module, "extract_metadata", _metadata)

    async def _run() -> None:
        db, service = await _service(tmp_path)
        source = tmp_path / "running.pdf"
        source.write_bytes(b"%PDF-1.7\nrunning")
        response = await service.request_parse(str(source), tier="standard", consumer_key="business:running")
        acquired = await service.acquire_task()
        assert acquired is not None and acquired["id"] == response.wait_parse_ids[0]
        released = await service.release_consumer("business:running")
        assert released.results[0].disposition == "running"
        assert released.results[0].status_at_release == "parsing"
        assert await db.fetchone("SELECT status FROM parses WHERE id=?", (acquired["id"],)) == {"status": "parsing"}

    asyncio.run(_run())


def test_finished_work_is_not_reported_as_queued_stop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parse_svc_module, "extract_metadata", _metadata)

    async def _run() -> None:
        db, service = await _service(tmp_path)
        source = tmp_path / "finished.pdf"
        source.write_bytes(b"%PDF-1.7\nfinished")
        response = await service.request_parse(str(source), tier="standard", consumer_key="business:finished")
        await db.execute("UPDATE parses SET status='done', done_at=1 WHERE id=?", (response.wait_parse_ids[0],))
        released = await service.release_consumer("business:finished")
        assert released.results[0].disposition == "finished"
        assert released.results[0].status_at_release == "done"
        assert await db.fetchone("SELECT status FROM parses WHERE id=?", (response.wait_parse_ids[0],)) == {"status": "done"}

    asyncio.run(_run())


def test_watched_source_keeps_unshared_pending_work_conservatively(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parse_svc_module, "extract_metadata", _metadata)

    async def _run() -> None:
        db, service = await _service(tmp_path)
        source = tmp_path / "watched.pdf"
        source.write_bytes(b"%PDF-1.7\nwatched")
        response = await service.request_parse(str(source), tier="standard", consumer_key="business:watched")
        await db.execute(
            "INSERT INTO watches (id, path, created_at, updated_at) VALUES (8, ?, 1, 1)",
            (str(tmp_path),),
        )
        await db.execute("UPDATE files SET watch_id=8 WHERE path=?", (str(source),))
        released = await service.release_consumer("business:watched")
        assert released.results[0].disposition == "retained"
        assert await db.fetchone("SELECT status FROM parses WHERE id=?", (response.wait_parse_ids[0],)) == {"status": "pending"}

    asyncio.run(_run())


def test_release_and_worker_acquire_have_one_serialized_winner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parse_svc_module, "extract_metadata", _metadata)

    async def _run() -> None:
        db, service = await _service(tmp_path)
        other_db = DatabaseManager(db.db_path)
        worker = ParseService(
            db=other_db,
            fts=FTSManager(other_db),
            config_svc=_NoRulesConfig(),
            data_dir=str(tmp_path / "data"),
            parse_lock_timeout_sec=1800,
        )
        source = tmp_path / "race.pdf"
        source.write_bytes(b"%PDF-1.7\nrace")
        response = await service.request_parse(str(source), tier="standard", consumer_key="business:race")
        released, acquired = await asyncio.gather(service.release_consumer("business:race"), worker.acquire_task())
        disposition = released.results[0].disposition
        if disposition == "skipped":
            assert acquired is None
            assert await db.fetchone("SELECT status FROM parses WHERE id=?", (response.wait_parse_ids[0],)) == {
                "status": "skipped"
            }
        else:
            assert disposition == "running"
            assert acquired is not None and acquired["id"] == response.wait_parse_ids[0]
            assert await db.fetchone("SELECT status FROM parses WHERE id=?", (acquired["id"],)) == {"status": "parsing"}

    asyncio.run(_run())


def test_release_before_request_tombstones_unknown_intent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parse_svc_module, "extract_metadata", _metadata)

    async def _run() -> None:
        db, service = await _service(tmp_path)
        source = tmp_path / "late.pdf"
        source.write_bytes(b"%PDF-1.7\nlate")
        assert (await service.release_consumer("business:late")).results == []
        with pytest.raises(InvalidRequestError) as error:
            await service.request_parse(str(source), tier="standard", consumer_key="business:late")
        assert error.value.code == "consumer_released"
        assert await db.fetchone("SELECT COUNT(*) AS count FROM parses") == {"count": 0}

    asyncio.run(_run())


def test_later_background_discovery_requeues_skipped_work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parse_svc_module, "extract_metadata", _metadata)

    async def _run() -> None:
        db, service = await _service(tmp_path)
        source = tmp_path / "later.pdf"
        source.write_bytes(b"%PDF-1.7\nlater")
        first = await service.request_parse(str(source), tier="standard", consumer_key="business:first")
        assert (await service.release_consumer("business:first")).results[0].disposition == "skipped"
        await service.ingest_file(str(source), trigger="background")
        rows = await db.fetchall("SELECT id, status FROM parses ORDER BY id")
        assert rows == [
            {"id": first.wait_parse_ids[0], "status": "skipped"},
            {"id": first.wait_parse_ids[0] + 1, "status": "pending"},
        ]
        assert await db.fetchone("SELECT consumer_key FROM parse_consumers WHERE parse_id=?", (rows[1]["id"],)) == {
            "consumer_key": "system:ingest"
        }

    asyncio.run(_run())
