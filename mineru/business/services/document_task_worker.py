"""Recover open document tasks without relying on a browser polling each task."""

from __future__ import annotations

import logging
import threading

from ..store import BusinessStore
from .documents import DocumentWorkflow

_LOG = logging.getLogger(__name__)


class DocumentTaskWorker:
    """Bounded, restartable scanner; Doclib generations make duplicate submissions safe."""

    def __init__(self, workflow: DocumentWorkflow, store: BusinessStore, *, poll_seconds: float = 2.0) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        self._workflow = workflow
        self._store = store
        self._poll_seconds = poll_seconds
        self._after_id = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("Document task worker already started")
        self._stop.clear()
        self._after_id = ""
        self._thread = threading.Thread(target=self._loop, name="business-documents", daemon=True)
        self._thread.start()

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
            if thread.is_alive():
                _LOG.warning("Document task worker did not stop before shutdown timeout")
            else:
                self._thread = None

    def run_once(self) -> bool:
        task_ids = self._store.list_recoverable_task_ids(after_id=self._after_id, limit=1)
        if not task_ids:
            self._after_id = ""
            return False
        task_id = task_ids[0]
        self._after_id = task_id
        task = self._store.get_task(task_id)
        if task is None:
            return True
        if task.status == "uploaded" or (task.status == "failed" and task.error_code == "doclib_submission_failed"):
            self._workflow.retry(task_id)
        else:
            self._workflow.refresh(task_id)
        return True

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                processed = self.run_once()
            except Exception:
                _LOG.exception("Document task recovery iteration failed")
                processed = False
            if not processed:
                self._stop.wait(self._poll_seconds)


__all__ = ["DocumentTaskWorker"]
