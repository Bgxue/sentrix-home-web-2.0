"""Verify the real-test command against actual pytest reports, including missing resources."""

from __future__ import annotations

import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("body", "expected_exit", "report_tag"),
    [
        ("assert True", 0, None),
        ("pytest.skip('required local model is unavailable')", 1, "skipped"),
        ("assert False, 'existing defect must stay red'", 1, "failure"),
    ],
)
def test_real_gate_requires_executed_passing_evidence(
    tmp_path: Path, body: str, expected_exit: int, report_tag: str | None
) -> None:
    root = Path(__file__).resolve().parents[4]
    test_file = tmp_path / "test_probe.py"
    test_file.write_text(
        "import pytest\n@pytest.mark.real_environment\ndef test_resource():\n    " + body + "\n"
    )
    report = tmp_path / "result.xml"
    result = subprocess.run(
        [sys.executable, str(root / "scripts/run_real_tests.py"), str(test_file), "--junitxml", str(report)],
        cwd=root,
        env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == expected_exit, result.stdout + result.stderr
    cases = ET.parse(report).getroot().findall(".//testcase")
    assert len(cases) == 1
    if report_tag:
        assert cases[0].find(report_tag) is not None
    else:
        assert "zero skips" in result.stdout


def test_real_gate_rejects_empty_selection_and_preserves_existing_report(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[4]
    test_file = tmp_path / "test_unmarked.py"
    test_file.write_text("def test_unmarked():\n    assert True\n")
    report = tmp_path / "result.xml"
    command = [
        sys.executable,
        str(root / "scripts/run_real_tests.py"),
        str(test_file),
        "--junitxml",
        str(report),
    ]
    env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    result = subprocess.run(command, env=env, capture_output=True, timeout=30, check=False)
    assert result.returncode == 5  # pytest 的零用例退出码原样保留
    original = report.read_bytes()
    result = subprocess.run(command, env=env, capture_output=True, timeout=30, check=False)
    assert result.returncode == 2
    assert b"Refusing to overwrite" in result.stderr
    assert report.read_bytes() == original
