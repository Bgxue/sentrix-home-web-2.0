"""Bounded process-local observations. Restart means unavailable, never an old live job."""

from __future__ import annotations

from collections import OrderedDict
from threading import Lock

from sentrix.domain.media.progress import ImportProgress


class InProcessImportProgressStore:
    def __init__(self, *, active_limit: int = 32, history_limit: int = 128) -> None:
        self._active_limit = active_limit
        self._history_limit = history_limit
        self._active: dict[str, ImportProgress] = {}
        self._history: OrderedDict[str, ImportProgress] = OrderedDict()
        self._lock = Lock()

    def create(self, progress: ImportProgress) -> None:
        with self._lock:
            if progress.operation_id in self._active or progress.operation_id in self._history:
                raise ValueError("import operation_id already exists")
            if len(self._active) >= self._active_limit:
                raise OverflowError("too many active tracked imports")
            self._active[progress.operation_id] = progress

    def save(self, progress: ImportProgress) -> None:
        with self._lock:
            if progress.operation_id not in self._active:
                raise KeyError(progress.operation_id)
            if progress.status == "running":
                self._active[progress.operation_id] = progress
            else:
                del self._active[progress.operation_id]
                self._history[progress.operation_id] = progress
                while len(self._history) > self._history_limit:
                    self._history.popitem(last=False)

    def get(self, operation_id: str) -> ImportProgress | None:
        with self._lock:
            return self._active.get(operation_id) or self._history.get(operation_id)
