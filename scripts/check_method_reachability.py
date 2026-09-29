#!/usr/bin/env python3
"""端口方法级可达性：**每个端口方法都要有生产调用者**。

## 为什么另起一条，而不是改 ``check_reachability.py``

那条问的是「**这个模块**是不是生产代码」，它答得对，而且 ``PENDING_WIRING`` 的双向
纪律（登记了但真接线了就报红）值得原样保留。这条问的是下一层的问题：「**这个方法**
有没有人调」。两个问题不同，判据也不同，混进一个脚本会让两边都变难改。

## 为什么存在

``check_reachability.py`` 的判定单位是模块名。于是 ``repositories/facts.py`` 因为
``container.py`` 构造了 ``SqliteFactRepository`` 就算「已接线」——**它里面 ``list_facts``
有没有人调，那个脚本从没问过**。2026-09-29 的审计正是从这个缝里找出五个零调用方法：

    FactRepository.list_facts / set_status / supersede_others
    RelationshipRepository.confirm / retract

它们的共同形态是「**读路径写成死代码**」：写侧接好了、读侧写好了、没人接。
而这种东西**不会报错**，只会让某个界面永远空着。

## 判据：唯一性代替类型推断

模块级能收紧成「这个名字是从那个模块 import 进来的、而且被读用过」，因为模块级有
**import 绑定**这个锚。方法级没有：``self._faces.list_clusters()`` 是**实例属性的
属性访问**，要知道 ``self._faces`` 是 ``FaceRepository`` 得靠类型推断——那是 mypy 的活，
不是一次 AST 走查能做的。

退一步的可行判据是「**这个名字在所有端口里唯一，且在别处被属性访问过**」。
实测（2026-09-29）：136 个端口方法里 **120 个名字唯一**，16 个重名。五个已知的
零调用方法里，**五个都可查**（``list_for`` 与 ``create`` 落在重名那边，查不了）。

**唯一性的方向性要说清**：它挡的是**误报**，不是漏报。一个唯一名字若恰好在非端口类上
也被用了，这条会漏掉那个死方法——**漏报是可接受的，误报会让门禁被关掉**。
所以判据宁松勿严，重名的 16 个**打印出来**当作已知盲区，而不是假装它们不存在。

## ``PENDING_METHODS`` 是例外表，不是后门

一个端口方法可能是「本切片先落地、下一个切片才接线」。这类必须逐个登记并写明
**哪个任务会接线**；同时另有一条反向规则：登记里的方法一旦真被调用了，检查会报红
要求删掉它。与 ``check_reachability.py`` 的 ``PENDING_WIRING`` 是同一条纪律。
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = REPO_ROOT / "apps" / "api" / "src"

# 端口方法 -> 会接上它的任务。**每个条目都必须写明任务**，否则它就是一个说不出理由的
# 例外，而说不出理由的例外名单会把这条不变量重新变成摆设。
#
# 前四条来自 2026-09-29 的覆盖审计（人工查出来的）；**后十条是这条门禁第一次跑就报出来的**
# ——模块级判据看不见它们（那些模块因为别的方法被用了而算「已接线」）。它们分两类：
#
#   - 能归到本计划任务上的，写任务号；
#   - 归不上的，写「S6 · 依赖核查」。这不是含糊其辞：按 S5c 的 T86g 体例，
#     动工前先核依赖、核出缺口再写任务定义，**核查本身就是一个有交付物的任务**。
#     把它们硬塞进一个编号会更像有归属，实际只是把问题推后。
PENDING_METHODS: dict[str, str] = {
    # —— 审计查出（2026-09-29）——
    "FactRepository.list_facts": "Task 113（facts 读路径）",
    "FactRepository.set_status": "Task 114（facts 状态机闭合）",
    "FactRepository.supersede_others": "Task 114（facts 状态机闭合）",
    "RelationshipRepository.retract": "Task 116（关系 CRUD）",
    # —— 本门禁首跑查出 ——
    "EntityRepository.list_properties": "Task 128（S6 语义实体：实体属性读）",
    "EntityRepository.maintain_property": "Task 128（S6 语义实体：实体属性写）",
    "RelationshipRepository.list_person_relationships": "S7（家庭关系，设计文档 §8）",
    "EventRepository.dissolve": "S7（重分段；s5-deviations 第 15/35 条记「随 S7」）",
    "PortraitRepository.list_for_person": "S6 · 依赖核查（画像版本列表，归属未定）",
    "PortraitRepository.set_lock": "S6 · 依赖核查（画像手改/锁定，归属未定）",
    "ObservationRepository.list_by_asset": "S6 · 依赖核查（观察读路径，归属未定）",
    "MediaStorage.path_for": "S6 · 依赖核查（媒体路径解析，归属未定）",
    "LexicalIndex.rebuild_scope": "S6 · 依赖核查（词法索引重建，归属未定）",
    "AssetRepository.update_status": "S6 · 依赖核查（资产状态流转，归属未定）",
}


def iter_protocols(src: Path):
    """产出 (文件, 协议类名, [方法名])。只看 ``domain/**/ports.py``。"""
    for path in sorted(src.rglob("domain/**/ports.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if not any("Protocol" in ast.unparse(base) for base in node.bases):
                continue
            methods = [
                item.name
                for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and not item.name.startswith("_")
            ]
            yield path, node.name, methods


def shadowing_names(src: Path) -> set[str]:
    """**会让属性访问解析到非端口对象**的方法名。

    ## 这个集合是怎么定出来的（两次修正）

    **第一版**只看端口之间的重名。``confirm`` 判成唯一——可它撞上了
    ``ReviewCluster.confirm``（``application/identity/review_cluster.py``），
    而全仓只有那两处 ``.confirm(`` 调用点，都不是仓储的。判据于是把
    ``RelationshipRepository.confirm`` 判成「已接线」，**反向规则随即要求删掉一条
    正确的登记**。所以「唯一性只漏报、不误报」是错的：**漏报会被反向规则放大成误报**。

    **第二版**要求全源码唯一。**更糟**——协议声明一次、实现类再定义一次，每个端口
    方法天然至少两个定义者，149 个方法名全被判重名，检查直接空转。

    **第三版（本版）**：撞名只可能来自**能把属性访问解析到端口之外的那些类**。
    实现类不继承协议（结构化类型），所以按继承识别行不通；但可以按位置分：

    - ``domain/**/ports.py`` 里的协议 —— 方法本来就该在这；
    - ``infrastructure/`` 下的类 —— 是端口的实现，同名不是撞名；
    - **其余位置**（``application/`` / ``api/`` / ``bootstrap/`` / ``domain`` 非 ports）
      的类若定义同名方法，才是真正的撞名来源。

    实测：这一版的盲区从 149 降到与第一版同量级，而 ``confirm`` 这类误判被挡住。
    """
    shadows: set[str] = set()
    for path in src.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(src)
        parts = relative.parts
        # **按「in parts」判，不按 parts[0]。** 调用方传的 ``src`` 是 ``apps/api/src``，
        # 于是 ``parts[0]`` 是包名 ``sentrix`` 而不是 ``infrastructure``——写成
        # ``parts[0] == "infrastructure"`` 时这条判断**永远不成立**，实现类（如
        # ``SqliteFactRepository.list_facts``）就全被当成遮蔽来源，136 个方法名一起
        # 变盲区、检查空转，而输出读起来完全正常。
        if "infrastructure" in parts:
            continue
        # 同理不用 ``Path.match("domain/**/ports.py")``：``**`` 在 match 里不展开成
        # 「任意层」，实测不命中 ``domain/memories/ports.py``。
        if path.name == "ports.py" and "domain" in parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    shadows.add(item.name)
    return shadows


def attribute_names(src: Path) -> set[str]:
    """生产代码里所有被读用的属性名（``x.foo`` 的 ``foo``）。"""
    used: set[str] = set()
    for path in src.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                used.add(node.attr)
    return used


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", default=str(DEFAULT_SRC))
    parser.add_argument(
        "--pending",
        action="append",
        default=[],
        metavar="CLASS.method=TASK",
        help="临时登记一个待接线的方法（测试用；真实登记写在本文件的 PENDING_METHODS 里）",
    )
    args = parser.parse_args()
    pending = dict(PENDING_METHODS)
    for item in args.pending:
        method, _, task = item.partition("=")
        pending[method] = task

    src = Path(args.src)
    used = attribute_names(src)

    owners: dict[str, list[str]] = defaultdict(list)
    for path, klass, methods in iter_protocols(src):
        for method in methods:
            owners[method].append(f"{path.relative_to(src)}::{klass}")

    # 撞名的两种来源：端口之间重名，以及被非端口类遮蔽（理由见 ``shadowing_names``）
    shadows = shadowing_names(src)
    collided = {name for name, where in owners.items() if len(where) > 1} | (shadows & set(owners))

    problems: list[str] = []
    for name, where in sorted(owners.items()):
        if name in collided:
            continue  # 重名 -> 判据不成立，进盲区（下面统一打印）
        if name in used:
            # 反向规则：登记了但真接线了，登记必须删掉
            for owner in where:
                key = f"{owner.rsplit('::', 1)[-1]}.{name}"
                if key in pending:
                    problems.append(
                        f"{key}: 已经接上生产了，但 PENDING_METHODS 里还留着它的条目"
                        f"（登记的接线任务是 {pending[key]}）——删掉这条"
                    )
            continue
        for owner in where:
            key = f"{owner.rsplit('::', 1)[-1]}.{name}"
            if key not in pending:
                problems.append(
                    f"{key}: 端口方法没有任何生产调用者（全仓没有一处属性访问用到这个名字）"
                    f"——接线，或写进 PENDING_METHODS 并注明哪个任务会接"
                )

    for problem in problems:
        print(problem)
    print(f"method-reachability: {len(problems)} 个问题")
    # 盲区要打印出来，不能假装不存在——否则「0 个问题」会被读成「全都查过了」。
    print(
        f"已知盲区：{len(collided)} 个方法名在多个端口重名，唯一性判据不成立，本检查不覆盖："
        f"{'、'.join(sorted(collided))}"
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
