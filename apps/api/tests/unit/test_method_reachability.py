"""``check_method_reachability.py`` 的反向门禁。

**与 ``test_reachability.py`` 同一条纪律**：不止断言「真实仓库是绿的」，还要造出
**应该报红的最小情形**，逐条确认 checker 真的会红；再造出**不该报红的情形**，
确认它不是恒红。没有下面这几条，「每个端口方法都要有生产调用者」就只是脚本里的一段注释。

其中三条来自这条门禁自己的返工（见 ``check_method_reachability.py`` 的注释）：
判据的第一版把 ``RelationshipRepository.confirm`` 误判成「已接线」，
第二版把 149 个方法名一起变盲区。**两条都写成了用例**——它们不是历史，是这条判据的边界。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT = "scripts/check_method_reachability.py"

PORTS = """from typing import Protocol


class ThingRepository(Protocol):
    def fetch(self) -> str: ...

    def store(self, value: str) -> None: ...
"""


def run_checker(src_root: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [".venv/bin/python", SCRIPT, "--src", str(src_root), *extra],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def build_tree(tmp_path: Path, caller_body: str, extra: dict[str, str] | None = None) -> Path:
    """最小 src 树：协议定义两个方法，调用方按 ``caller_body`` 决定用不用。

    位置是有讲究的——``part`` 的名字决定它算不算「遮蔽来源」：
    ``infrastructure/`` 下的是端口实现（同名不算撞名），其余位置才算。
    """
    src_root = tmp_path / "src"
    sentrix = src_root / "sentrix"
    (sentrix / "domain" / "thing").mkdir(parents=True)
    (sentrix / "__init__.py").write_text("", encoding="utf-8")
    (sentrix / "domain" / "__init__.py").write_text("", encoding="utf-8")
    (sentrix / "domain" / "thing" / "__init__.py").write_text("", encoding="utf-8")
    (sentrix / "domain" / "thing" / "ports.py").write_text(PORTS, encoding="utf-8")
    (sentrix / "application.py").write_text(caller_body, encoding="utf-8")
    for relative, body in (extra or {}).items():
        target = sentrix / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return src_root


def test_flags_a_port_method_with_no_caller(tmp_path):
    """要防的那个形状：端口方法写好了、没人调。"""
    src_root = build_tree(tmp_path, "x = 1\n")

    result = run_checker(src_root)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "ThingRepository.fetch: 端口方法没有任何生产调用者" in result.stdout
    assert "ThingRepository.store: 端口方法没有任何生产调用者" in result.stdout


def test_a_called_method_passes(tmp_path):
    """反向对照：真的被调了就不该报。没有这条，上面那条可能只是「永远报红」。"""
    src_root = build_tree(tmp_path, "thing.fetch()\nthing.store('a')\n")

    result = run_checker(src_root)

    assert result.returncode == 0, result.stdout + result.stderr


def test_an_implementation_defining_the_method_is_not_a_collision(tmp_path):
    """**实现类定义同名方法不算撞名。** 端口是结构化类型，实现不继承协议。

    这条是返工出来的：判据第一版按「parts[0] == 'infrastructure'」跳过实现，
    而调用方传的 ``--src`` 是 ``apps/api/src``，``parts[0]`` 其实是包名——
    判断**永远不成立**，于是每个端口方法都被自己的实现「遮蔽」，检查整片空转、
    输出却读起来完全正常。用例把实现放进 ``infrastructure/`` 就是钉这件事。
    """
    src_root = build_tree(
        tmp_path,
        "x = 1\n",
        extra={"infrastructure/thing.py": "class SqliteThing:\n    def fetch(self): ...\n"},
    )

    result = run_checker(src_root)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "ThingRepository.fetch: 端口方法没有任何生产调用者" in result.stdout


def test_a_name_shadowed_by_a_non_port_class_is_blind_not_wrong(tmp_path):
    """**撞名要进盲区，不能报错、更不能被当成「已接线」。**

    这条也是返工出来的：``RelationshipRepository.confirm`` 曾被判成「已接线」，
    因为全仓唯一的 ``.confirm(`` 调用点是 ``ReviewCluster.confirm``——
    一个 ``application/`` 下的类。反向规则随即要求删掉一条**正确**的登记。
    判据因此改成：能让属性访问解析到端口之外的类，才算遮蔽来源。
    """
    src_root = build_tree(
        tmp_path,
        "other.fetch()\n",
        extra={"other.py": "class Other:\n    def fetch(self): ...\n"},
    )

    result = run_checker(src_root)

    assert "ThingRepository.fetch" not in result.stdout, result.stdout
    assert "已知盲区" in result.stdout
    assert "fetch" in result.stdout.split("已知盲区")[1]


def test_a_declared_pending_method_is_not_flagged(tmp_path):
    src_root = build_tree(tmp_path, "x = 1\n")

    result = run_checker(
        src_root,
        "--pending",
        "ThingRepository.fetch=Task 999",
        "--pending",
        "ThingRepository.store=Task 999",
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_a_stale_pending_entry_is_flagged(tmp_path):
    """例外表不能变成墓碑：接上生产之后，登记必须删掉。

    与 ``check_reachability.py`` 的 ``PENDING_WIRING``、``test_container_wiring.py``
    的 ``EXCEPTIONS`` 是同一条纪律。没有这条，登记会只增不减。
    """
    src_root = build_tree(tmp_path, "thing.fetch()\nthing.store('a')\n")

    result = run_checker(
        src_root,
        "--pending",
        "ThingRepository.fetch=Task 999",
        "--pending",
        "ThingRepository.store=Task 999",
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "已经接上生产了" in result.stdout
    assert "PENDING_METHODS 里还留着它的条目" in result.stdout


def test_real_repository_has_no_problems():
    """拿仓库真实的源码树跑一遍——规则要在 pytest 里也生效，不能只在 make ci 里。"""
    result = subprocess.run(
        [".venv/bin/python", SCRIPT],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
