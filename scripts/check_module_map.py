#!/usr/bin/env python3
"""校验 module-map.md 与真实代码一致。

1.0 的 docs/PROJECT_MEMORY.md 列了 16 个根本不存在的模块文件。这个脚本确保同样的事
不会再发生：源码里每个 .py 文件必须在文档里有条目，反之亦然。

依赖边同样被校验，而不是只写在文档的说明文字里：「依赖谁」由 ast 从真实的包内 import
推出，「被谁依赖」是它的转置——两列都由脚本从同一份解析结果推导后比对，所以不存在
「文档自称校验了、实际没人读」的列。只统计 `sentrix.*` 包内 import；stdlib 与第三方
（fastapi / sqlite3 / PIL 等）不进这两列。

包的 ``__init__.py`` 也是一条边：``import sentrix.domain.retrieval.scheme`` 这类子模块导入
会被同时记成两条依赖——``sentrix/domain/retrieval/scheme.py`` **和**
``sentrix/domain/retrieval/__init__.py``（导入子模块必然先执行包的 ``__init__``，而
``__init__`` 自己也有 import）。所以新文件只要落在某个包下，被它依赖的那两行「被谁依赖」列
都要加——少加包 ``__init__.py`` 那行同样会红。

列数不足的行会被报错而不是跳过：跳过等于给依赖校验开后门——那行写什么都不会红。

路径基准只有一份：``--src`` 传 **`src/` 根目录**（如 ``apps/api/src``），表格里写的也正是
相对它的路径（如 ``sentrix/config.py``）。以前 ``--src`` 传的是包目录而输出相对其父目录，
同一件事两个基准，调用方很容易传错而看不出来。
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = REPO_ROOT / "apps" / "api" / "src"
DEFAULT_DOC = REPO_ROOT / "docs" / "architecture" / "module-map.md"
EMPTY_CELL = "—"
MIN_COLUMNS = 5  # 路径 / 职责 / 层 / 依赖谁 / 被谁依赖
HEADER_RE = re.compile(r"^\|\s*路径\s*\|")
ROW_RE = re.compile(r"^\|\s*`([^`]+\.py)`\s*\|(.*)\|\s*$")
CELL_PATH_RE = re.compile(r"`([^`]+)`")


def relative(path: Path, src: Path) -> str:
    """表格里写的路径形态：相对 ``src/`` 根目录，如 ``sentrix/config.py``。"""
    return str(path.relative_to(src)).replace("\\", "/")


def module_name(path: Path, src: Path) -> str:
    """文件路径 -> 绝对模块名；``__init__.py`` 归到包本身。"""
    parts = list(path.relative_to(src).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def package_of(path: Path, src: Path) -> str:
    """相对 import 的解析基准：普通模块取所在包，``__init__.py`` 取包自身。"""
    module = module_name(path, src)
    return module if path.name == "__init__.py" else module.rpartition(".")[0]


def source_paths(src: Path) -> set[str]:
    return {relative(path, src) for path in src.rglob("*.py") if "__pycache__" not in path.parts}


def imported_modules(path: Path, src: Path) -> set[str]:
    """文件里所有 import 涉及的绝对模块名（含相对 import 解析后的结果）。"""
    package = package_of(path, src)
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package
                for _ in range(node.level - 1):
                    base = base.rpartition(".")[0]
                module = f"{base}.{node.module}" if node.module else base
            else:
                module = node.module or ""
            names.add(module)
            # ``from pkg import mod`` 在 mod 是子模块时会把 pkg.mod 也导进来
            names.update(f"{module}.{alias.name}" for alias in node.names)
    return names


def dependency_edges(src: Path) -> dict[str, set[str]]:
    """每个文件 -> 它真实依赖的包内文件集合。"""
    files = {path: module_name(path, src) for path in src.rglob("*.py") if "__pycache__" not in path.parts}
    by_module = {module: path for path, module in files.items()}

    edges: dict[str, set[str]] = {}
    for path in files:
        edges[relative(path, src)] = {
            relative(target, src)
            for name in imported_modules(path, src)
            if (target := by_module.get(name)) is not None and target != path
        }
    return edges


def documented_paths(doc: Path) -> set[str]:
    """从 markdown 表格的第一列提取路径。"""
    text = doc.read_text(encoding="utf-8") if doc.exists() else ""
    paths = set()
    for line in text.splitlines():
        match = ROW_RE.match(line)
        if match:
            paths.add(match.group(1))
    return paths


def documented_rows(doc: Path) -> list[str]:
    """表格第一列的路径，**按出现顺序、保留重复**。

    ``documented_paths`` 返回的是 set，重复路径在集合里只留一份——于是「同一行写了
    两遍」在这条校验里根本不是一个可观测量。这个函数专门把「重复」变成看得见的东西。
    """
    text = doc.read_text(encoding="utf-8") if doc.exists() else ""
    rows: list[str] = []
    for line in text.splitlines():
        match = ROW_RE.match(line)
        if match:
            rows.append(match.group(1))
    return rows


def duplicate_rows(doc: Path) -> list[str]:
    """出现超过一次的第一列路径，按首次出现顺序。"""
    seen: set[str] = set()
    duplicates: list[str] = []
    for path in documented_rows(doc):
        if path in seen and path not in duplicates:
            duplicates.append(path)
        seen.add(path)
    return duplicates


def row_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def parse_cell(path: str, column: str, cell: str, problems: list[str]) -> set[str]:
    """一格依赖列：要么是 `—`，要么是反引号路径用 `、` 分隔。"""
    if cell == EMPTY_CELL:
        return set()
    names = CELL_PATH_RE.findall(cell)
    leftover = CELL_PATH_RE.sub("", cell).strip(" 、")
    if not names or leftover:
        problems.append(f"{path}: 「{column}」列格式非法，应为 {EMPTY_CELL} 或反引号路径 -> {cell!r}")
        return set()
    return set(names)


def documented_edges(doc: Path) -> tuple[dict[str, set[str]], dict[str, set[str]], list[str]]:
    """从表格解析「依赖谁」「被谁依赖」两列（0 基下标 3 / 4）。"""
    deps: dict[str, set[str]] = {}
    dependents: dict[str, set[str]] = {}
    problems: list[str] = []
    text = doc.read_text(encoding="utf-8") if doc.exists() else ""

    expected = MIN_COLUMNS
    for line in text.splitlines():
        if HEADER_RE.match(line):
            expected = len(row_cells(line))
            if expected < MIN_COLUMNS:
                problems.append(f"表头只有 {expected} 列，缺少「依赖谁」/「被谁依赖」——这两列不能被省略")
            continue

        match = ROW_RE.match(line)
        if not match:
            continue

        path = match.group(1)
        cells = row_cells(line)
        if len(cells) != expected:
            problems.append(f"{path}: 行有 {len(cells)} 列，与表头的 {expected} 列不一致")
            continue
        if expected < MIN_COLUMNS:
            continue  # 表头已经报错，这一行没有依赖列可读

        deps[path] = parse_cell(path, "依赖谁", cells[3], problems)
        dependents[path] = parse_cell(path, "被谁依赖", cells[4], problems)
    return deps, dependents, problems


def compare(path: str, column: str, real: set[str], documented: set[str], problems: list[str]) -> None:
    for missing in sorted(real - documented):
        problems.append(f"{path}: 「{column}」缺少 {missing}")
    for extra in sorted(documented - real):
        problems.append(f"{path}: 「{column}」多出 {extra}")


def main() -> int:
    parser = argparse.ArgumentParser(description="校验 module-map.md 与真实代码一致")
    parser.add_argument(
        "--src",
        default=str(DEFAULT_SRC),
        help="src/ 根目录（默认 apps/api/src）。表格里的路径相对它书写，如 sentrix/config.py",
    )
    parser.add_argument(
        "--doc",
        default=str(DEFAULT_DOC),
        help="module-map.md 路径（默认 docs/architecture/module-map.md）",
    )
    args = parser.parse_args()

    src = Path(args.src)
    doc = Path(args.doc)

    in_code = source_paths(src)
    in_doc = documented_paths(doc)

    problems: list[str] = []
    # 重复行必须在这里拦下来，不能靠后面的集合/字典比对——那两个容器会把重复吃掉：
    # documented_paths() 是 set（重复只留一份），documented_edges() 是 dict（后一行
    # 覆盖前一行）。两条逐字节相同的行因此「写几遍都一样」，一条不同的行也只是让
    # 覆盖后那一份被比到。**校验模型里不存在的量，就是没被校验的量。**
    duplicates = duplicate_rows(doc)
    rows = documented_rows(doc)
    if duplicates:
        problems.append(
            f"module-map 有重复行：{len(rows)} 行、{len(set(rows))} 个唯一路径，重复的是 {duplicates}"
        )
    for missing in sorted(in_code - in_doc):
        problems.append(f"代码里有但 module-map 未记录: {missing}")
    for stale in sorted(in_doc - in_code):
        problems.append(f"module-map 有但代码里不存在: {stale}")

    edges = dependency_edges(src)
    dependents: dict[str, set[str]] = {path: set() for path in edges}
    for path, deps in edges.items():
        for dep in deps:
            dependents[dep].add(path)

    doc_deps, doc_dependents, cell_problems = documented_edges(doc)
    problems.extend(cell_problems)

    for path in sorted(set(edges) & set(doc_deps)):
        compare(path, "依赖谁", edges[path], doc_deps[path], problems)
    for path in sorted(set(dependents) & set(doc_dependents)):
        compare(path, "被谁依赖", dependents[path], doc_dependents[path], problems)

    for problem in sorted(problems):
        print(problem)
    print(f"module-map: {len(problems)} 个问题")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
