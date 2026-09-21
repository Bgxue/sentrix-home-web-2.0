import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]


def test_import_linter_contracts_are_declared():
    result = subprocess.run(
        [".venv/bin/lint-imports", "--contract", "domain-purity"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    # lint-imports 把诊断写 stdout 而非 stderr，所以必须正面确认契约真的被找到了：
    # 配置读不到 / 契约名对不上 / TOML 写错，三者都会让 returncode=1 却不进 stderr。
    output = result.stdout + result.stderr
    assert result.returncode in (0, 1), output
    assert "domain-purity" in output, output
    assert "Could not find contract" not in output, output


def test_domain_does_not_import_infrastructure():
    """domain 若依赖 infrastructure，下面这条断言会先失败。"""
    domain = REPO_ROOT / "apps/api/src/sentrix/domain"
    offenders = [
        path
        for path in domain.rglob("*.py")
        if "from sentrix.infrastructure" in path.read_text(encoding="utf-8")
        or "import sentrix.infrastructure" in path.read_text(encoding="utf-8")
    ]

    assert offenders == []
