"""BEH-37: контроль качества проходит на заявленном стеке (живой запуск)."""

import os
import re
import subprocess
import tempfile
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.integration


def _clean_env() -> dict[str, str]:
    """Окружение без ключей доступа: проверки не должны их требовать."""
    secret = re.compile(r"(API_KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)", re.I)
    return {k: v for k, v in os.environ.items() if not secret.search(k)}


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "--frozen", *args],
        cwd=ROOT,
        env=_clean_env(),
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_ruff_check_passes() -> None:
    result = _run("ruff", "check", ".")
    assert result.returncode == 0, result.stdout + result.stderr


def test_ruff_format_passes() -> None:
    result = _run("ruff", "format", "--check", ".")
    assert result.returncode == 0, result.stdout + result.stderr


def test_ruff_enforces_line_length_88() -> None:
    ok = 'X = "' + "a" * 82 + '"\n'  # ровно 88 символов
    bad = 'X = "' + "a" * 83 + '"\n'  # 89 символов
    assert len(ok.rstrip("\n")) == 88 and len(bad.rstrip("\n")) == 89
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "ok.py").write_text(ok)
        (Path(tmp) / "bad.py").write_text(bad)
        args = ("ruff", "check", "--config", str(ROOT / "pyproject.toml"))
        good = _run(*args, "--select", "E501", str(Path(tmp) / "ok.py"))
        over = _run(*args, "--select", "E501", str(Path(tmp) / "bad.py"))
    assert good.returncode == 0, good.stdout + good.stderr
    assert over.returncode != 0, "строка в 89 символов должна нарушать E501"


def test_pyrefly_passes() -> None:
    result = _run("pyrefly", "check")
    assert result.returncode == 0, result.stdout + result.stderr


def test_pytest_collects_without_errors() -> None:
    result = _run("pytest", "--collect-only", "-q", "tests")
    assert result.returncode == 0, result.stdout + result.stderr


def test_stack_declared_python312_uv_anyio() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert data["project"]["requires-python"].startswith(">=3.12")
    deps = " ".join(data["project"]["dependencies"])
    assert "anyio" in deps
    assert (ROOT / "uv.lock").is_file()


def test_no_asyncio_entrypoints_in_source() -> None:
    offenders = [
        str(p.relative_to(ROOT))
        for p in (ROOT / "src").rglob("*.py")
        if re.search(r"\basyncio\.(run|gather|create_task)\b", p.read_text())
    ]
    assert offenders == []


def test_no_pip_in_instructions_and_scripts() -> None:
    pip = re.compile(r"\bpip3?\b(?!line)", re.I)
    files = [ROOT / "README.md", *(ROOT / "scripts").rglob("*")]
    files += [ROOT / "pyproject.toml"]
    hits = []
    for f in files:
        if not f.is_file():
            continue
        for n, line in enumerate(f.read_text(errors="ignore").splitlines(), 1):
            if pip.search(line) and "pipefail" not in line:
                hits.append(f"{f.relative_to(ROOT)}:{n}")
    assert hits == []
