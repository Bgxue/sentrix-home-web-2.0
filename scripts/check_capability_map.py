#!/usr/bin/env python3
"""校验 capability-map.jsonl。

强制三件事（设计 §7）：
1. 每条 status=verified 的记录，verification 指向的文件必须真实存在
2. capability 名唯一：同一能力不能登记两次（一个文件实现多个能力是允许的）
3. origin_kind=frozen 的记录必须在 frozen.md 里有理由
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VALID_KINDS = {"migrated", "adapted", "NEW-2.0", "frozen"}
VALID_STATUS = {"verified", "pending", "frozen"}
FROZEN_DOC = REPO_ROOT / "docs" / "refactor" / "frozen.md"


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def check(entries: list[dict]) -> list[str]:
    problems: list[str] = []
    frozen_text = FROZEN_DOC.read_text(encoding="utf-8") if FROZEN_DOC.exists() else ""
    seen_names: set[str] = set()

    for entry in entries:
        name = entry.get("capability", "<unnamed>")
        if entry.get("origin_kind") not in VALID_KINDS:
            problems.append(f"{name}: origin_kind 非法 {entry.get('origin_kind')!r}")
        if entry.get("status") not in VALID_STATUS:
            problems.append(f"{name}: status 非法 {entry.get('status')!r}")

        if name in seen_names:
            problems.append(f"{name}: capability 名重复，已在别处登记")
        seen_names.add(name)

        if entry.get("status") == "verified":
            verification = entry.get("verification")
            if not verification:
                problems.append(f"{name}: status=verified 但没有 verification")
            elif not (REPO_ROOT / verification).exists():
                problems.append(f"{name}: verification 文件不存在 -> {verification}")

        if entry.get("origin_kind") == "frozen" and name not in frozen_text:
            problems.append(f"{name}: 已冻结但未在 docs/refactor/frozen.md 说明理由")

    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--map", default=str(REPO_ROOT / "docs/refactor/capability-map.jsonl"))
    args = parser.parse_args()

    problems = check(load(Path(args.map)))
    for problem in problems:
        print(problem)
    print(f"capability-map: {len(problems)} 个问题")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
