import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]


def test_module_map_matches_real_imports():
    result = subprocess.run(
        [".venv/bin/python", "scripts/check_module_map.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_checker_flags_undocumented_module(tmp_path):
    fake_src = tmp_path / "src" / "sentrix"
    fake_src.mkdir(parents=True)
    (fake_src / "orphan.py").write_text("x = 1\n")
    doc = tmp_path / "module-map.md"
    doc.write_text("| 路径 | 职责 | 层 |\n|---|---|---|\n", encoding="utf-8")

    result = subprocess.run(
        [".venv/bin/python", "scripts/check_module_map.py", "--src", str(fake_src.parent), "--doc", str(doc)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "orphan.py" in result.stdout


def test_checker_flags_wrong_dependency_columns(tmp_path):
    """反向验证：依赖列写错必须报红。

    没有这一条，上面那条只证明了「文件集合」是真的在查，「依赖谁 / 被谁依赖」两列
    仍可能是恒真的摆设——而那正是本脚本存在的理由之一。
    """
    src = tmp_path / "src"
    package = src / "sentrix"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "config.py").write_text('"""Config."""\n', encoding="utf-8")
    (package / "main.py").write_text("from sentrix.config import Settings\n", encoding="utf-8")
    doc = tmp_path / "module-map.md"
    doc.write_text(
        "| 路径 | 职责 | 层 | 依赖谁 | 被谁依赖 |\n"
        "|---|---|---|---|---|\n"
        "| `sentrix/__init__.py` | 包标记 | — | — | — |\n"
        "| `sentrix/config.py` | 配置 | — | — | — |\n"
        "| `sentrix/main.py` | 入口 | — | — | — |\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [".venv/bin/python", "scripts/check_module_map.py", "--src", str(package), "--doc", str(doc)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "sentrix/main.py: 「依赖谁」缺少 sentrix/config.py" in result.stdout
    assert "sentrix/config.py: 「被谁依赖」缺少 sentrix/main.py" in result.stdout
