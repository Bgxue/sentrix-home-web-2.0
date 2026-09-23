#!/usr/bin/env python3
"""每个模块必须在生产装配路径上找得到一个调用者。

**为什么要有这条检查。** S4 栽过两次同一个形状：Task 66/67 交付了两块「代码在、
没人调」的能力——一条零调用的纯规则，和一个零调用的 ``SummarizeEvent``。两次都是
事后被用户点出来的，说明**靠人记是记不住的**。这条把它变成可机械判定的东西。

**判据不是「被 import 过」，是「被用过」。** 只查 import 会漏掉 ``SummarizeEvent``
那种情形：它确实被 ``container.py`` import 了（于是「零 import」检查是绿的），
但从来没有人构造它。所以对每个模块还要看它的**公开符号有没有被别的生产文件
name-load / 调用过**。协议类（``Protocol``）只用作类型注解，注解在 AST 里同样是
``ast.Name`` 节点，所以这条对端口模块成立。

**两个根**：``sentrix.main``（进程入口）与 ``sentrix.bootstrap``。只有从根**可达**的
文件才算生产代码；测试与 ``scripts/`` 不在图里——它们不是调用者。

**PENDING_WIRING 是显式的例外表，不是后门。** 一个模块可能是「本切片先落地、
下一个切片才接线」（例如 Task 69 的纯规则要等 Task 72/79 才被调用）。
这类模块必须逐个登记，并写明**哪个任务会接线**；同时另有一条规则保证这些登记
不会变成墓碑：登记里的模块一旦真的被调用了，检查会报红要求删掉它。
（与 ``test_container_wiring.py`` 的 ``EXCEPTIONS`` 是同一条纪律。）
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = REPO_ROOT / "apps" / "api" / "src"
ROOTS = ("sentrix.main", "sentrix.bootstrap")

# 模块 -> 会把它接上生产的任务。**每个条目都必须写明任务，否则它就是一个说不出
# 理由的例外**——而说不出理由的例外名单会把这条不变量重新变成摆设。
PENDING_WIRING: dict[str, str] = {
    "sentrix.domain.identity.faces": (
        "Task 84（全局重聚类的维护入口）会构造 FaceClusterer；Task 72 只用了它的四个"
        "纯函数（归一化 / 余弦 / 质量 / 姿态桶），而 Task 72 的用例本身还没接线，"
        "所以它仍不可达。它是 Task 69 落地的纯规则，S5a 之前没有别的调用者。"
    ),
    "sentrix.application.identity.enroll_face": (
        "Task 75（HTTP 路由 + PersonDirectory 实现）与 Task 79（导入路径接线）会构造它，"
        "那时它才有生产调用者。Task 72 只落地用例与它的仓储写方法。"
    ),
    "sentrix.application.identity.review_cluster": (
        "Task 75 的四个簇审阅端点（confirm / reject / merge / split）会构造它。"
        "Task 73 只落地用例与它的仓储写方法，S5a 的这一轮还没有路由。"
    ),
    "sentrix.infrastructure.models.onnx_runtime": (
        "Task 78 的 ``face_adapter`` 会用它建 buffalo_l 的 session，Task 79 再把人脸链路"
        "接进导入路径。Task 77 只落地检测器与它的 provider 预算。"
    ),
    "sentrix.infrastructure.models.face_detector": (
        "Task 78 的 ``face_adapter`` 会构造 ``RetinaFaceTiledDetector`` 并复用它的"
        "``bbox_agreement`` / ``landmark_sanity`` / ``expand_crop``；Task 79 把整条链路接进"
        "导入路径。Task 77 只落地检测器本身（纯几何在 make ci 里验，真权重那条会真跑）。"
    ),
    "sentrix.application.identity.name_person": (
        "Task 75 的 ``/api/people`` 改名端点与 batch-confirm 端点会构造它。Task 74 的"
        "用例被 ``ReviewCluster.confirm`` 之外的另一条链路使用（按实体确认），而在只做"
        "用例的 S5a 这一轮还没有路由。"
    ),
}


def module_name(path: Path, src: Path) -> str:
    parts = list(path.relative_to(src).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def package_of(path: Path, src: Path) -> str:
    module = module_name(path, src)
    return module if path.name == "__init__.py" else module.rpartition(".")[0]


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
            names.update(f"{module}.{alias.name}" for alias in node.names)
    return names


def used_names(path: Path) -> set[str]:
    """文件里所有被**读**的名字。``ast.Name`` 覆盖注解与调用，``ast.Attribute`` 覆盖
    ``module.Symbol`` 这种形态。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            names.add(node.id)
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            names.add(node.attr)
    return names


def bound_names(path: Path, src: Path, known: set[str]) -> dict[str, str]:
    """这个文件里**被导入绑定**的本地名 -> 它指向的模块全名。

    - ``import a.b.c`` / ``import a.b.c as x`` -> 本地名 ``c`` / ``x``，指向 ``a.b.c``
    - ``from pkg import name`` -> 若 ``pkg.name`` 是一个已知模块（子模块导入），指向
      ``pkg.name``；否则指向 ``pkg``（那是一个符号导入）

    **为什么不能只在一堆裸名字上取交集。** 第一版是「把所有 ``ast.Name`` 载入名与该模块的
    公开符号求交」，结果是：某个函数的形参叫 ``pose_bucket``，就与
    ``domain/identity/faces.py`` 的公开函数 ``pose_bucket`` 撞上，那个模块于是被判成
    「已经接上生产」——**一个假阴性**，而 PENDING_WIRING 的反向规则还会因此报错要求
    删掉一条本来正确的登记。同一族的假阴性还有常量名撞车（``VISUAL_SPACE``）。

    判据收紧成「**这个名字是从那个模块导入进来的，而且被读用过**」：
    形参、局部变量、恰好同名的常量都不再算数。名字空间由导入关系给出，不由拼写给出。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = package_of(path, src)
    binding: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                binding[alias.asname or alias.name.split(".")[-1]] = alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package
                for _ in range(node.level - 1):
                    base = base.rpartition(".")[0]
                module = f"{base}.{node.module}" if node.module else base
            else:
                module = node.module or ""
            for alias in node.names:
                candidate = f"{module}.{alias.name}"
                target = candidate if candidate in known else module
                binding[alias.asname or alias.name] = target
    return binding


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", default=str(DEFAULT_SRC))
    # 与 check_module_map.py 的 --src / --doc 同一个理由：例外表如果不能被测试注入，
    # 这条检查自己就没有反向门禁，而「没有反向门禁的检查」正是它要防的那种东西。
    parser.add_argument(
        "--pending",
        action="append",
        default=[],
        metavar="MODULE=TASK",
        help="临时登记一个待接线模块（测试用；真实登记写在本文件的 PENDING_WIRING 里）",
    )
    args = parser.parse_args()
    pending = dict(PENDING_WIRING)
    for item in args.pending:
        module, _, task = item.partition("=")
        pending[module] = task

    src = Path(args.src)
    files = {path: module_name(path, src) for path in src.rglob("*.py") if "__pycache__" not in path.parts}
    by_module = {module: path for path, module in files.items()}

    edges: dict[Path, set[Path]] = {}
    used: dict[Path, set[str]] = {}
    bound: dict[Path, dict[str, str]] = {}
    for path in files:
        edges[path] = {
            target
            for name in imported_modules(path, src)
            if (target := by_module.get(name)) is not None and target != path
        }
        used[path] = used_names(path)
        bound[path] = bound_names(path, src, set(by_module))

    reachable: set[Path] = set()
    frontier = [by_module[name] for name in ROOTS if name in by_module]
    while frontier:
        current = frontier.pop()
        if current in reachable:
            continue
        reachable.add(current)
        frontier.extend(edges[current])

    problems: list[str] = []
    for path in sorted(files, key=lambda item: files[item]):
        name = files[path]
        if name.startswith(ROOTS) or path.name == "__init__.py":
            continue
        callers = {
            other
            for other in reachable
            if other != path
            and any(target == name and local in used[other] for local, target in bound[other].items())
        }
        wired = bool(callers)
        if not wired and name not in pending:
            problems.append(
                f"{name}: 没有任何生产调用者（从 {' / '.join(ROOTS)} 可达的文件里没有一处"
                f" 导入并用过它的名字）——接线，或写进 PENDING_WIRING 并注明哪个任务会接"
            )
        if wired and name in pending:
            problems.append(
                f"{name}: 已经接上生产了（调用者 {sorted(files[c] for c in callers)[:3]}），"
                f"但 PENDING_WIRING 里还留着它的条目（登记的接线任务是 {pending[name]}）——删掉这条"
            )

    for problem in problems:
        print(problem)
    print(f"reachability: {len(problems)} 个问题")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
