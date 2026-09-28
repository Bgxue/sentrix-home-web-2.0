"""思考通道只能有一个入口——**机械判定，不靠人记**。

``<|channel>thought`` 的剥离在 ``infrastructure/assistant/chat_client.py`` 的
``split_thought_channel``，它就在构造 ``ModelReply`` 的那一处之前几行。实测全仓
``ModelReply(`` 只有这一个构造点。

**为什么这条要变成用例。** 构造点会随重构漂移：有人给流式回复加一条旁路、有人把
规划那一支拆成单独的客户端——那一刻没有任何用例会红，用户只会看到气泡里冒出一串
内部标记（2026-09-25 就是这么发现的，当时已经上线跑过）。

用户 2026-09-28 裁决：**不在前端补第二道剥离**，而是把后端这道门做成可判定的。
两处独立实现同一个语义正是本仓库一路在防的形状——第二个剥离点迟早与第一个漂开。
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "sentrix"

CONSTRUCTOR = "ModelReply"
GUARD = "split_thought_channel"


def _iter_calls() -> list[tuple[Path, ast.AST, ast.FunctionDef | ast.AsyncFunctionDef | None]]:
    """全仓扫一遍，产出 (文件, 调用节点, 所在函数)。"""
    found: list[tuple[Path, ast.AST, ast.FunctionDef | ast.AsyncFunctionDef | None]] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # 每个函数体分别遍历，好把调用节点归到它的函数上
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for child in ast.walk(node):
                if (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Name)
                    and child.func.id == CONSTRUCTOR
                ):
                    found.append((path, child, node))
    return found


def test_the_constructor_has_exactly_one_door() -> None:
    """全仓 ``ModelReply(`` 只许有一处。

    两处就意味着有一条旁路，而旁路里有没有剥离只看写的人记不记得。
    """
    sites = _iter_calls()
    where = [f"{path.relative_to(SRC)}:{call.lineno}" for path, call, _ in sites]
    assert len(sites) == 1, f"ModelReply 有 {len(sites)} 个构造点，只允许一个: {where}"


def test_that_door_goes_through_the_guard() -> None:
    """而且那一处必须在**调过** ``split_thought_channel`` 的函数里。

    只看「构造点唯一」不够：唯一的那一处完全可以是新写的、没剥离的。
    """
    (path, call, function) = _iter_calls()[0]
    assert function is not None, f"{path}:{call.lineno} 的构造不在任何函数里？"
    source = ast.get_source_segment(path.read_text(encoding="utf-8"), function) or ""
    assert GUARD in source, (
        f"{path.relative_to(SRC)} 的 {function.name} 构造了 ModelReply 却没调 {GUARD}——思考通道会漏进用户答案"
    )


def test_the_guard_itself_still_exists() -> None:
    """剥离函数本身还在（改名/删掉时这条先红，比上面两条的报错好读）。"""
    text = (SRC / "infrastructure/assistant/chat_client.py").read_text(encoding="utf-8")
    assert f"def {GUARD}(" in text, f"chat_client.py 里找不到 {GUARD} 的定义"
