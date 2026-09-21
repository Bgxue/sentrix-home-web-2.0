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
