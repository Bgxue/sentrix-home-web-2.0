import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
CONTRACT_COUNT = 5


def test_layering_contracts_all_hold():
    """跑全量契约，并要求「一条都没坏」。

    以前写的是 ``assert result.returncode in (0, 1)``——契约 BROKEN 时 returncode 正是 1，
    三条断言全过，于是这条测试永远不会因分层被破坏而失败。
    """
    result = subprocess.run(
        [".venv/bin/lint-imports"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert f"Contracts: {CONTRACT_COUNT} kept, 0 broken" in output, output


def test_domain_purity_contract_is_addressable_by_name():
    """契约能被按名寻址——配置读不到 / 契约名写错时上面那条会红，但这里定位更直接。"""
    result = subprocess.run(
        [".venv/bin/lint-imports", "--contract", "domain-purity"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "domain-purity KEPT" in output, output


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
