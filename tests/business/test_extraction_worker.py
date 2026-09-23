"""Worker lifecycle preserves queue claims during shutdown and transient failures."""

from __future__ import annotations

import logging
import threading
from unittest.mock import Mock

import pytest

from mineru.business.services import ExtractionWorker
from mineru.business.services.extractions import FieldExtraction


def test_worker_rejects_nonpositive_poll_interval() -> None:
    with pytest.raises(ValueError, match="poll_seconds must be positive"):
        ExtractionWorker(Mock(spec=FieldExtraction), poll_seconds=0)


def test_worker_stops_idle_polling_and_can_restart() -> None:
    first_poll = threading.Event()
    second_poll = threading.Event()
    calls = 0

    def process_next() -> None:
        nonlocal calls
        calls += 1
        (first_poll if calls == 1 else second_poll).set()
        return None

    extraction = Mock(spec=FieldExtraction)
    extraction.process_next.side_effect = process_next
    worker = ExtractionWorker(extraction, poll_seconds=60)
    try:
        worker.start()
        assert first_poll.wait(2)
        with pytest.raises(RuntimeError, match="already started"):
            worker.start()
        worker.stop()
        assert calls == 1

        worker.start()
        assert second_poll.wait(2)
    finally:
        worker.stop()
    assert calls == 2


def test_worker_logs_unexpected_error_and_continues_polling(caplog: pytest.LogCaptureFixture) -> None:
    retried = threading.Event()
    calls = 0

    def process_next() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary storage failure")
        retried.set()
        return None

    extraction = Mock(spec=FieldExtraction)
    extraction.process_next.side_effect = process_next
    worker = ExtractionWorker(extraction, poll_seconds=0.01)
    with caplog.at_level(logging.ERROR):
        try:
            worker.start()
            assert retried.wait(2)
        finally:
            worker.stop()
    assert calls >= 2
    assert "Business extraction worker iteration failed" in caplog.text


def test_timeout_does_not_start_second_worker_while_first_claim_is_running(
    caplog: pytest.LogCaptureFixture,
) -> None:
    entered = threading.Event()
    release = threading.Event()

    def process_next() -> object:
        entered.set()
        assert release.wait(2)
        return object()

    extraction = Mock(spec=FieldExtraction)
    extraction.process_next.side_effect = process_next
    worker = ExtractionWorker(extraction, poll_seconds=60)
    try:
        worker.start()
        assert entered.wait(2)
        with caplog.at_level(logging.WARNING):
            worker.stop(timeout=0.01)
        assert "did not stop before shutdown timeout" in caplog.text
        with pytest.raises(RuntimeError, match="already started"):
            worker.start()
    finally:
        release.set()
        worker.stop()
    assert extraction.process_next.call_count == 1
