import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
HEADER = "| 路径 | 职责 | 层 | 依赖谁 | 被谁依赖 |\n|---|---|---|---|---|\n"


def run_checker(src_root: Path, doc: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [".venv/bin/python", "scripts/check_module_map.py", "--src", str(src_root), "--doc", str(doc)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def test_module_map_matches_real_imports():
    result = subprocess.run(
        [".venv/bin/python", "scripts/check_module_map.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_checker_flags_undocumented_module(tmp_path):
    """--src 是 src/ 根目录，表格路径也相对它——断言钉住完整路径，别只钉文件名。"""
    src_root = tmp_path / "src"
    package = src_root / "sentrix"
    package.mkdir(parents=True)
    (package / "orphan.py").write_text("x = 1\n")
    doc = tmp_path / "module-map.md"
    doc.write_text(HEADER, encoding="utf-8")

    result = run_checker(src_root, doc)

    assert result.returncode == 1
    assert "代码里有但 module-map 未记录: sentrix/orphan.py" in result.stdout


def test_checker_rejects_the_package_dir_as_src_root(tmp_path):
    """按旧约定把包目录当 --src 传，必须报错，不能静默换一套基准。

    这是本脚本最危险的失败模式：基准换掉了，结论照给，人以为校验通过了。
    传错时路径会整体丢掉 `sentrix/` 前缀，下面两条断言钉的就是这个错位形态。
    """
    src_root = tmp_path / "src"
    package = src_root / "sentrix"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "config.py").write_text('"""Config."""\n', encoding="utf-8")
    doc = tmp_path / "module-map.md"
    doc.write_text(
        HEADER
        + "| `sentrix/__init__.py` | 包标记 | — | — | — |\n"
        + "| `sentrix/config.py` | 配置 | — | — | — |\n",
        encoding="utf-8",
    )

    correct = run_checker(src_root, doc)
    assert correct.returncode == 0, correct.stdout + correct.stderr

    wrong_base = run_checker(package, doc)

    assert wrong_base.returncode == 1, wrong_base.stdout + wrong_base.stderr
    assert "代码里有但 module-map 未记录: __init__.py" in wrong_base.stdout
    assert "module-map 有但代码里不存在: sentrix/__init__.py" in wrong_base.stdout
    assert "module-map 有但代码里不存在: sentrix/config.py" in wrong_base.stdout


def test_checker_flags_wrong_dependency_columns(tmp_path):
    """反向验证：依赖列写错必须报红。

    没有这一条，上面那条只证明了「文件集合」是真的在查，「依赖谁 / 被谁依赖」两列
    仍可能是恒真的摆设——而那正是本脚本存在的理由之一。
    """
    src_root = tmp_path / "src"
    package = src_root / "sentrix"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "config.py").write_text('"""Config."""\n', encoding="utf-8")
    (package / "main.py").write_text("from sentrix.config import Settings\n", encoding="utf-8")
    doc = tmp_path / "module-map.md"
    doc.write_text(
        HEADER
        + "| `sentrix/__init__.py` | 包标记 | — | — | — |\n"
        + "| `sentrix/config.py` | 配置 | — | — | — |\n"
        + "| `sentrix/main.py` | 入口 | — | — | — |\n",
        encoding="utf-8",
    )

    result = run_checker(src_root, doc)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "sentrix/main.py: 「依赖谁」缺少 sentrix/config.py" in result.stdout
    assert "sentrix/config.py: 「被谁依赖」缺少 sentrix/main.py" in result.stdout


def test_checker_flags_row_with_missing_dependency_columns(tmp_path):
    """列数不足的行必须报错，不能被整行跳过。

    跳过等于给依赖校验留后门：那行写什么都不会红，而文档又照样接受它。
    """
    src_root = tmp_path / "src"
    package = src_root / "sentrix"
    package.mkdir(parents=True)
    (package / "config.py").write_text('"""Config."""\n', encoding="utf-8")
    doc = tmp_path / "module-map.md"
    doc.write_text(HEADER + "| `sentrix/config.py` | 配置 | — |\n", encoding="utf-8")

    result = run_checker(src_root, doc)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "sentrix/config.py: 行有 3 列，与表头的 5 列不一致" in result.stdout


def test_checker_flags_duplicate_rows(tmp_path):
    """重复行必须报红——这是这个 checker 原先的一个洞。

    洞的形状不是「判据不覆盖重复行」，而是「判据的容器把重复行吃掉了」：
    ``documented_paths()`` 返回 ``set``（重复只留一份），``documented_edges()``
    返回 ``dict``（后一行覆盖前一行）。两条**逐字节相同**的行因此「写几遍都一样」，
    而内容不同的两条也只是让覆盖后那一份被比到——文件集合那一半连这都抓不到。
    **校验模型里不存在的量，就是没被校验的量。**

    真实来历：``ee690e1``（Task 64）把那一行从 0 加到 1，``9f14c5d``（Task 65）
    从 1 加到 2，位置是 ``docs/architecture/module-map.md`` 的第 66 / 67 行，
    两行 ``md5sum`` 相同，两种状态 checker 都报 0 个问题。
    """
    src_root = tmp_path / "src"
    package = src_root / "sentrix"
    package.mkdir(parents=True)
    (package / "config.py").write_text('"""Config."""\n', encoding="utf-8")
    row = "| `sentrix/config.py` | 配置 | — | — | — |\n"
    doc = tmp_path / "module-map.md"
    doc.write_text(HEADER + row + row, encoding="utf-8")

    result = run_checker(src_root, doc)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "module-map 有重复行" in result.stdout
    assert "sentrix/config.py" in result.stdout


def test_checker_accepts_a_table_without_duplicates(tmp_path):
    """反向对照：不重复时不能报。没有这条，上面那条可能只是「永远报红」。"""
    src_root = tmp_path / "src"
    package = src_root / "sentrix"
    package.mkdir(parents=True)
    (package / "config.py").write_text('"""Config."""\n', encoding="utf-8")
    doc = tmp_path / "module-map.md"
    doc.write_text(HEADER + "| `sentrix/config.py` | 配置 | — | — | — |\n", encoding="utf-8")

    result = run_checker(src_root, doc)

    assert result.returncode == 0, result.stdout + result.stderr
