"""Single-process poller for the SQLite-backed extraction queue."""

from __future__ import annotations

import logging
import threading

from .extractions import FieldExtraction

_LOG = logging.getLogger(__name__)


class ExtractionWorker:
    """Process queued runs outside HTTP requests; SQLite leases coordinate restarts."""

    def __init__(self, extraction: FieldExtraction, *, poll_seconds: float = 1.0) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        self._extraction = extraction
        self._poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("Extraction worker already started")
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="business-extraction", daemon=True)
        self._thread.start()

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
            if thread.is_alive():
                _LOG.warning("Business extraction worker did not stop before shutdown timeout")
            else:
                self._thread = None

    def run_once(self) -> bool:
        return self._extraction.process_next() is not None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                processed = self.run_once()
            except Exception:
                # The lease expires after an unexpected process error; the next poll retries safely.
                _LOG.exception("Business extraction worker iteration failed")
                processed = False
            if not processed:
                self._stop.wait(self._poll_seconds)


__all__ = ["ExtractionWorker"]
