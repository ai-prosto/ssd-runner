"""BEH-14: обмен данными идёт только через файлы; сеть — только адаптер SDK."""

import ast
import socket
from pathlib import Path

import pytest

from sprc_runner.cli import LoopPorts, main
from sprc_runner.doubles import (
    EchoAgent,
    FakeReviewer,
    FakeSpecChecker,
    FakeTestRunner,
)

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "sprc_runner"

NETWORK_QUEUE_DB = (
    "socket",
    "ssl",
    "http",
    "httpx",
    "requests",
    "aiohttp",
    "websockets",
    "urllib.request",
    "pika",
    "kombu",
    "redis",
    "sqlite3",
    "psycopg",
    "psycopg2",
    "pymongo",
    "sqlalchemy",
)
"""Сетевые клиенты, очереди и базы данных, недопустимые в инструменте."""

SDK_MODULE = "claude_agent_sdk"
SDK_ADAPTER = SRC / "adapters" / "claude_sdk.py"


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def _matches(name: str, forbidden: str) -> bool:
    return name == forbidden or name.startswith(forbidden + ".")


def test_no_network_queue_or_db_clients_anywhere() -> None:
    files = sorted(SRC.rglob("*.py"))
    assert files, "исходники инструмента не найдены"
    for module in files:
        offending = {
            name
            for name in _imports(module)
            for forbidden in NETWORK_QUEUE_DB
            if _matches(name, forbidden)
        }
        rel = module.relative_to(SRC)
        assert not offending, f"{rel} импортирует {sorted(offending)}"


def test_sdk_imported_only_in_adapter() -> None:
    for module in sorted(SRC.rglob("*.py")):
        has_sdk = any(_matches(name, SDK_MODULE) for name in _imports(module))
        if module == SDK_ADAPTER:
            assert has_sdk, "адаптер SDK не импортирует SDK"
        else:
            assert not has_sdk, f"{module.relative_to(SRC)} импортирует SDK напрямую"


def test_full_run_exchanges_data_only_through_files(
    tmp_path: Path, config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Прогон целиком при запрете сети, портов и соединений: только файлы."""

    def refuse(*args: object, **kwargs: object) -> object:
        raise AssertionError("прогон обратился к сети, порту или соединению")

    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket.socket, "bind", refuse)
    monkeypatch.setattr(socket.socket, "listen", refuse)

    code = main(
        ["run", "описание через файлы", "--config", str(config_file)],
        cwd=tmp_path,
        agent=EchoAgent(),
        loop=LoopPorts(FakeReviewer(), FakeTestRunner(), FakeSpecChecker()),
    )
    assert code == 0

    (run_dir,) = (tmp_path / ".sdd-runs" / ".sprc").iterdir()
    for name in ("input.md", "spec.md", "requirements.md", "tasks.md", "state.json"):
        assert (run_dir / name).is_file(), f"обмен не виден как файл: {name}"
