"""Run explicit real-environment tests; missing dependencies cannot produce a green gate."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="Optional test files or node IDs to select by impact")
    parser.add_argument("--junitxml", type=Path, help="Retain the exact pytest result at this path")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    report = args.junitxml or Path(tempfile.mkdtemp(prefix="sentrix-real-tests-")) / "results.xml"
    report = report.resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    if report.exists():
        parser.error(f"Refusing to overwrite an existing report: {report}")
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-c",
        str(root / "pyproject.toml"),
        "-m",
        "real_environment",
        f"--junitxml={report}",
        *args.paths,
    ]
    print(f"Real integration report: {report}", flush=True)
    result = subprocess.run(command, cwd=root, check=False)
    if result.returncode:
        return result.returncode
    if not report.is_file():
        print("Real integration gate failed: no JUnit evidence", file=sys.stderr)
        return 1
    cases = ET.parse(report).getroot().findall(".//testcase")
    incomplete = [
        case for case in cases if any(case.find(tag) is not None for tag in ("skipped", "failure", "error"))
    ]
    if not cases or incomplete:
        print(
            f"Real integration gate failed: {len(cases)} cases, "
            f"{len(incomplete)} missing, skipped or failed; see {report}",
            file=sys.stderr,
        )
        return 1
    print(f"Real integration gate passed: {len(cases)} cases, zero skips; {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
