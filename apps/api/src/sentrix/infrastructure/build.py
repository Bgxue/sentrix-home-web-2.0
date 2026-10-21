"""Read the immutable build manifest shipped with a source release."""

from __future__ import annotations

import json
from pathlib import Path


def read_build_revision() -> str:
    try:
        payload = json.loads((Path(__file__).resolve().parents[5] / "build-info.json").read_text())
        return str(payload["revision"])
    except (OSError, ValueError, KeyError, TypeError):
        return "development"
