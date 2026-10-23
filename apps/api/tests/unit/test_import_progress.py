"""Bounded progress facts, duplicate protection and restart semantics."""

from dataclasses import replace

import pytest

from sentrix.application.media.track_import import TrackImport
from sentrix.domain.media.progress import ImportProgress
from sentrix.infrastructure.media.import_progress import InProcessImportProgressStore


def test_progress_reservation_precedes_side_effects_and_is_bounded():
    store = InProcessImportProgressStore(active_limit=1, history_limit=1)
    track = TrackImport(store, "one", "scope")
    with pytest.raises(ValueError):
        TrackImport(store, "one", "scope")
    with pytest.raises(OverflowError):
        TrackImport(store, "two", "scope")
    track.update(total=3, completed=3, imported=1, deduplicated=1, failed=1, stage="person_insights")
    current = store.get("one")
    assert current.status == "running" and current.completed == 3
    assert current.completed == current.imported + current.deduplicated + current.failed
    track.update(status="completed", stage="completed")
    assert current.status == "running"  # previous observations are immutable
    assert store.get("one").sequence == 2
    second = TrackImport(store, "two", "scope")
    second.update(status="failed", error="disk failure")
    assert store.get("one") is None
    assert store.get("two").error == "disk failure"
    assert InProcessImportProgressStore().get("two") is None


def test_terminal_or_unknown_observation_cannot_be_resurrected():
    store = InProcessImportProgressStore()
    progress = ImportProgress("one", "scope")
    store.create(progress)
    store.save(replace(progress, status="failed"))
    with pytest.raises(KeyError):
        store.save(progress)
