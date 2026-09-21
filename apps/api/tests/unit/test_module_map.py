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
