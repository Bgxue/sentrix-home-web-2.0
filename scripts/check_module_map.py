#!/usr/bin/env python3
"""校验 module-map.md 与真实代码一致。

1.0 的 docs/PROJECT_MEMORY.md 列了 16 个根本不存在的模块文件。这个脚本
确保同样的事不会再发生：源码里每个 .py 文件必须在文档里有条目，反之亦然。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = REPO_ROOT / "apps" / "api" / "src" / "sentrix"
DEFAULT_DOC = REPO_ROOT / "docs" / "architecture" / "module-map.md"


def documented_paths(doc: Path) -> set[str]:
    """从 markdown 表格的第一列提取路径。"""
    text = doc.read_text(encoding="utf-8") if doc.exists() else ""
    paths = set()
    for line in text.splitlines():
        match = re.match(r"^\|\s*`([^`]+\.py)`\s*\|", line)
        if match:
            paths.add(match.group(1))
    return paths


def source_paths(src: Path) -> set[str]:
    return {
        str(path.relative_to(src.parent)).replace("\\", "/")
        for path in src.rglob("*.py")
        if "__pycache__" not in path.parts
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", default=str(DEFAULT_SRC))
    parser.add_argument("--doc", default=str(DEFAULT_DOC))
    args = parser.parse_args()

    src = Path(args.src)
    doc = Path(args.doc)

    in_code = source_paths(src)
    in_doc = documented_paths(doc)

    problems = []
    for missing in sorted(in_code - in_doc):
        problems.append(f"代码里有但 module-map 未记录: {missing}")
    for stale in sorted(in_doc - in_code):
        problems.append(f"module-map 有但代码里不存在: {stale}")

    for problem in problems:
        print(problem)
    print(f"module-map: {len(problems)} 个问题")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
