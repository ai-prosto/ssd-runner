"""BEH-35: тесты ядра проходят без сети, без ключей доступа и без модуля SDK."""

import ast
import asyncio
import importlib
import socket
import sys
from pathlib import Path

import pytest

from sprc_runner.config import load_config
from sprc_runner.doubles import (
    FakeAgent,
    FakeReviewer,
    FakeSpecChecker,
    FakeTestRunner,
)

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "src" / "sprc_runner" / "core"

FORBIDDEN_IN_CORE = (
    "claude_agent_sdk",
    "anthropic",
    "subprocess",
    "socket",
    "ssl",
    "http",
    "urllib",
    "httpx",
    "requests",
    "aiohttp",
)
"""Прямые обращения к SDK, git и сетевым клиентам, недопустимые в ядре."""

TASK_MD = "### TASK-001: Первая\nP2 | ⬜ TODO   Est: 1d\n\n**Checklist:**\n- [ ] a\n"


def _imports(path: Path) -> set[str]:
    """Имена модулей, которые файл импортирует (без относительных импортов)."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def test_core_has_no_direct_sdk_git_or_network_imports() -> None:
    modules = sorted(CORE.glob("*.py"))
    assert modules, "модули ядра не найдены"
    for module in modules:
        offending = {
            name for name in _imports(module) if name.split(".")[0] in FORBIDDEN_IN_CORE
        }
        assert not offending, f"core/{module.name} импортирует {sorted(offending)}"


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Сеть отключена, ключей доступа нет, модуль SDK недоступен для импорта."""

    def refuse(*args: object, **kwargs: object) -> object:
        raise AssertionError("обращение к сети при отключённой сети")

    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", None)


def test_core_imports_and_task_cycle_pass_offline(
    offline: None, tmp_path: Path
) -> None:
    saved = {
        name: module
        for name, module in sys.modules.items()
        if name.startswith("sprc_runner.core")
    }
    for name in saved:
        del sys.modules[name]
    try:
        cycle = importlib.import_module("sprc_runner.core.task_cycle")
        config = load_config(ROOT / "sprc-runner.toml")
        tests = FakeTestRunner([1, 0])

        async def attempt(task, number):  # noqa: ANN001, ANN202
            return await cycle.run_attempt(
                agent=FakeAgent(),
                reviewer=FakeReviewer(),
                tests=tests,
                spec_checker=FakeSpecChecker(),
                config=config,
                task=task,
                number=number,
                workdir=tmp_path,
            )

        result = asyncio.run(cycle.run_tasks(TASK_MD, attempt, config.max_attempts))
        assert not result.aborted
        assert result.succeeded_count == 1
        assert tests.invocations == 2  # неуспех, затем успех — всё без сети
    finally:
        for name in [n for n in sys.modules if n.startswith("sprc_runner.core")]:
            del sys.modules[name]
        sys.modules.update(saved)
