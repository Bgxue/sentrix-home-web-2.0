"""``check_reachability.py`` 的反向门禁。

**这条检查本身要是恒真的，它就白写了。** 与 ``test_module_map.py`` 的立意相同：
不止断言「真实仓库是绿的」，还要造出**应该报红的最小情形**，逐条确认 checker 真的会红。
没有下面三条反向用例，「每个模块都要有生产调用者」这句话就只是脚本里的一段注释。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT = "scripts/check_reachability.py"


def run_checker(src_root: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [".venv/bin/python", SCRIPT, "--src", str(src_root), *extra],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def build_tree(tmp_path: Path, main_body: str, thing_body: str = "class Thing:\n    pass\n") -> Path:
    """一个最小的 src 树：``sentrix/main.py`` 是根，``sentrix/thing.py`` 是被查的模块。"""
    src_root = tmp_path / "src"
    package = src_root / "sentrix"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "main.py").write_text(main_body, encoding="utf-8")
    (package / "thing.py").write_text(thing_body, encoding="utf-8")
    return src_root


def test_flags_a_module_with_no_production_caller(tmp_path):
    """S4 两次栽跟头的那个形状：模块在、测试在、生产没人调。"""
    src_root = build_tree(tmp_path, "x = 1\n")

    result = run_checker(src_root)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "sentrix.thing: 没有任何生产调用者" in result.stdout


def test_a_used_module_passes(tmp_path):
    """反向对照：真的被用了就不该报。没有这条，上面那条可能只是「永远报红」。"""
    src_root = build_tree(tmp_path, "from sentrix.thing import Thing\n\nThing()\n")

    result = run_checker(src_root)

    assert result.returncode == 0, result.stdout + result.stderr


def test_a_merely_imported_module_is_still_flagged(tmp_path):
    """**这一条是这套检查存在的全部理由。**

    ``SummarizeEvent`` 当初就是「被 import 了、从来没人构造」——只查 import 的检查
    对它是绿的。所以判据必须是「公开符号被 name-load 过」，不是「模块被 import 过」。
    """
    src_root = build_tree(tmp_path, "from sentrix.thing import Thing\n\nx = 1\n")

    result = run_checker(src_root)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "sentrix.thing: 没有任何生产调用者" in result.stdout


def test_a_declared_pending_module_is_not_flagged(tmp_path):
    src_root = build_tree(tmp_path, "x = 1\n")

    result = run_checker(src_root, "--pending", "sentrix.thing=Task 99")

    assert result.returncode == 0, result.stdout + result.stderr


def test_a_stale_pending_entry_is_flagged(tmp_path):
    """例外表不能变成墓碑：接上生产之后，登记必须删掉。

    没有这条，``PENDING_WIRING`` 会只增不减，下一次「暂时先登记一下」就不会有人再看它。
    这与 ``test_container_wiring.py`` 的 ``EXCEPTIONS`` 是同一条纪律。
    """
    src_root = build_tree(tmp_path, "from sentrix.thing import Thing\n\nThing()\n")

    result = run_checker(src_root, "--pending", "sentrix.thing=Task 99")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "已经接上生产了" in result.stdout
    assert "PENDING_WIRING 里还留着它的条目" in result.stdout


def test_real_repository_has_no_problems():
    """拿仓库真实的源码树跑一遍——规则要在 pytest 里也生效，不能只在 make ci 里。"""
    result = subprocess.run(
        [".venv/bin/python", SCRIPT],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_a_same_named_parameter_does_not_count_as_a_caller(tmp_path):
    """**这条是第一版判据的一个假阴性，实施 Task 72 时被撞出来的。**

    第一版是「把所有 ``ast.Name`` 载入名与该模块的公开符号求交」。于是只要有人在别处
    写一个**形参**叫 ``pose_bucket``，``domain/identity/faces.py`` 那个同名公开函数就被
    当成「已经被用过了」——模块被判成已接生产，而 PENDING_WIRING 的反向规则还会因此
    报错要求删掉一条本来正确的登记。同一族的还有常量名撞车（``VISUAL_SPACE``）。

    判据因此收紧成「**这个名字是从那个模块导入进来的，而且被读用过**」：
    名字空间由导入关系给出，不由拼写给出。
    """
    src_root = build_tree(
        tmp_path,
        "def unrelated(pose_bucket):\n    return pose_bucket\n",
        "def pose_bucket(pose):\n    return 'frontal'\n",
    )

    result = run_checker(src_root)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "sentrix.thing: 没有任何生产调用者" in result.stdout


def test_a_from_imported_symbol_that_is_used_counts_as_a_caller(tmp_path):
    """反向对照：真的 ``from ... import`` 进来并用过，就必须算数。"""
    src_root = build_tree(
        tmp_path,
        "from sentrix.thing import pose_bucket\n\nx = pose_bucket(None)\n",
        "def pose_bucket(pose):\n    return 'frontal'\n",
    )

    result = run_checker(src_root)

    assert result.returncode == 0, result.stdout + result.stderr
