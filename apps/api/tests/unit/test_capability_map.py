import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
CAPABILITY_MAP = REPO_ROOT / "docs" / "refactor" / "capability-map.jsonl"


def test_capability_map_entries_are_well_formed():
    lines = [
        json.loads(line) for line in CAPABILITY_MAP.read_text(encoding="utf-8").splitlines() if line.strip()
    ]

    assert lines, "capability-map 不能为空"
    for entry in lines:
        assert entry["origin_kind"] in {"migrated", "adapted", "NEW-2.0", "frozen"}
        assert entry["status"] in {"verified", "pending", "frozen"}
        assert entry["capability"]


def test_frozen_capabilities_are_documented():
    frozen = [
        json.loads(line)
        for line in CAPABILITY_MAP.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line)["origin_kind"] == "frozen"
    ]
    frozen_doc = (REPO_ROOT / "docs/refactor/frozen.md").read_text(encoding="utf-8")

    for entry in frozen:
        assert entry["capability"] in frozen_doc, f"{entry['capability']} 未在 frozen.md 说明理由"


def test_checker_rejects_entry_with_missing_verification_file(tmp_path):
    bad = tmp_path / "capability-map.jsonl"
    bad.write_text(
        json.dumps(
            {
                "capability": "不存在的验证",
                "origin_kind": "migrated",
                "source_commit": "2d2cee7c",
                "source_path": "backend/app.py",
                "source_symbol": "health",
                "target_path": "apps/api/src/sentrix/api/routes/health.py",
                "verification": "tests/contract/does_not_exist.py",
                "status": "verified",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [".venv/bin/python", "scripts/check_capability_map.py", "--map", str(bad)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "does_not_exist.py" in result.stdout


def test_real_capability_map_has_no_problems():
    """拿仓库真实的 capability-map 跑一遍 checker——规则要在 pytest 里也生效。

    上面那条用的是 tmp_path 造的假 map，只覆盖单条规则；没有这条，checker 的其它
    规则就只在 `make check-docs` 里才执行，单元测试永远是绿的。
    """
    result = subprocess.run(
        [".venv/bin/python", "scripts/check_capability_map.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout
