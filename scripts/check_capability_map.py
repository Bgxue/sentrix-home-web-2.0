#!/usr/bin/env python3
"""校验 capability-map.jsonl。

强制四件事（设计 §7）：
1. 每条 status=verified 的记录，verification 指向的文件必须真实存在
2. capability 名唯一：同一能力不能登记两次（一个文件实现多个能力是允许的）
3. origin_kind=frozen 的记录必须在 frozen.md 里有理由
4. **反向规则**：标着 ``pending`` 的，``target_path`` 不能已经存在

第 4 条是 S6 收口（T132）补的。前三条是**单向**的——它们能抓「声称验过、其实没有」，
**抓不到「其实验过、却还标着 pending」**。2026-09-29 的实测就撞上了：``person_moments``
与 ``person_portrait_revisions`` 卡在 ``pending``，而它们的实现在 S5c 就交付了
（线上库 8 行瞬间 / 2 篇画像）。一条只往一个方向看的门禁，会把「落后于事实的登记」
一直放过去。与 ``check_reachability.py`` 的 ``PENDING_WIRING``、
``check_method_reachability.py`` 的 ``PENDING_METHODS`` 是同一条纪律：
登记一旦被现实追上，门禁自己报红要求删。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
VALID_KINDS = {"migrated", "adapted", "NEW-2.0", "frozen"}
VALID_STATUS = {"verified", "pending", "frozen"}
FROZEN_DOC = REPO_ROOT / "docs" / "refactor" / "frozen.md"


def load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def check(entries: list[dict[str, Any]]) -> list[str]:
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

        # 反向规则：``pending`` 说的是「还没搬」，而 target_path 存在说的是「搬了」。
        # 两句话不能同时为真——真出现了，就是登记没跟上现实。
        if entry.get("status") == "pending":
            target = entry.get("target_path")
            if target and (REPO_ROOT / target).exists():
                problems.append(
                    f"{name}: 标着 pending，但 target_path 已经存在（{target}）"
                    f"——标成 verified 并补上 verification，或说明它为什么还没交付"
                )

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
