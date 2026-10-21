"""Single-instance result-root lease and auditable orphan recovery (Unix deployments)."""

from __future__ import annotations

import fcntl
import json
import logging
import os
import uuid
from pathlib import Path
from typing import IO

from photobench.domain.runs.analysis import now_iso

log = logging.getLogger(__name__)


class FileRunStartupRecovery:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._lease: IO[str] | None = None

    def acquire_and_recover(self) -> int:
        self._root.mkdir(parents=True, exist_ok=True)
        lease = (self._root / ".photobench-instance.lock").open("a+", encoding="utf-8")
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            lease.close()
            raise RuntimeError("PhotoBench results root already has a live owner") from error
        self._lease = lease
        lease.seek(0)
        lease.truncate()
        lease.write(str(os.getpid()))
        lease.flush()
        recovered = 0
        for path in sorted(self._root.glob("*/run.json")):
            try:
                original = path.read_bytes()
                state = json.loads(original)
                if not isinstance(state, dict):
                    continue
                active = state.get("status") in {
                    "running",
                    "pending",
                    "cancelling",
                }
                rejudge = state.get("rejudge")
                orphan_judge = isinstance(rejudge, dict) and rejudge.get("status") == "running"
                if not active and not orphan_judge:
                    continue
                backup = path.with_name("run.before-orphan-recovery.json")
                # A crash after backup but before replacement must remain recoverable.
                if backup.exists():
                    backup = path.with_name(f"run.before-orphan-recovery.{uuid.uuid4().hex}.json")
                with backup.open("xb") as handle:
                    handle.write(original)
                timestamp = now_iso()
                state["recovery"] = {
                    "reason": "previous_instance_lost",
                    "recovered_at": timestamp,
                    "original_status": state["status"],
                    "backup": backup.name,
                }
                if active:
                    state.update(status="interrupted", finished_at=timestamp)
                if orphan_judge and isinstance(rejudge, dict):
                    state["rejudge"] = {
                        **rejudge,
                        "status": "interrupted",
                        "finished_at": timestamp,
                        "error": "previous_instance_lost",
                    }
                temporary = path.with_suffix(".recovery.tmp")
                temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
                temporary.replace(path)
                recovered += 1
            except (OSError, ValueError):
                log.exception("Could not recover run state: %s", path)
        log.info(
            "PhotoBench instance %s owns %s; recovered %s orphan runs", os.getpid(), self._root, recovered
        )
        return recovered

    def close(self) -> None:
        if self._lease is not None:
            fcntl.flock(self._lease, fcntl.LOCK_UN)
            self._lease.close()
            self._lease = None
