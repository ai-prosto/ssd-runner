"""BEH-25: артефакты прогона читаются без запуска инструмента.

Прогон выполняется на подставном агенте и подставном раннере тестов,
после чего каталог прогона разбирается только стандартной библиотекой —
без импорта читателей инструмента — как это сделал бы сторонний парсер.
"""

import hashlib
import json
from pathlib import Path

import pytest

from sprc_runner.cli import LoopPorts, main
from sprc_runner.doubles import (
    EchoAgent,
    FakeReviewer,
    FakeSpecChecker,
    FakeTestRunner,
)

DESCRIPTION = "Эталонное описание для проверки читаемости каталога прогона"


@pytest.fixture
def completed_run_dir(tmp_path: Path, config_file: Path) -> Path:
    """Каталог завершённого прогона: одна задача, неуспешная и успешная попытки."""
    code = main(
        ["run", DESCRIPTION, "--config", str(config_file)],
        cwd=tmp_path,
        agent=EchoAgent(),
        loop=LoopPorts(FakeReviewer(), FakeTestRunner([1, 0]), FakeSpecChecker()),
    )
    assert code == 0
    (run_dir,) = (tmp_path / ".sdd-runs" / ".sprc").iterdir()
    return run_dir


def test_input_and_prepare_artifacts_readable(completed_run_dir: Path) -> None:
    assert (completed_run_dir / "input.md").read_bytes() == DESCRIPTION.encode()
    for name in ("spec.md", "requirements.md", "tasks.md"):
        text = (completed_run_dir / name).read_text(encoding="utf-8")
        assert text.strip(), f"артефакт подготовки пуст: {name}"
    tasks = (completed_run_dir / "tasks.md").read_text(encoding="utf-8")
    assert "TASK-001" in tasks and "DONE" in tasks  # отметка видна глазами


def test_state_restores_what_was_done_per_task(completed_run_dir: Path) -> None:
    state = json.loads((completed_run_dir / "state.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256(DESCRIPTION.encode()).hexdigest()
    assert state["input_sha256"] == digest
    assert all(stage["status"] == "done" for stage in state["stages"])
    (task,) = state["tasks"]
    assert task["task_id"] == "TASK-001"
    assert task["status"] == "done"
    assert task["attempts"] == 2  # первая попытка неуспешна, вторая успешна
    assert task["last_attempt"] == {
        "number": 2,
        "success": True,
        "error_code": None,
    }
    assert state["finished"]


def test_log_restores_attempt_history_per_task(completed_run_dir: Path) -> None:
    """В каталоге прогона лежит лог, и по нему видно ход попыток задачи."""
    log = completed_run_dir / "log.jsonl"
    assert log.is_file(), "лога нет в каталоге завершённого прогона"
    events = [
        json.loads(line)
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert events, "лог пуст"
    attempts = {
        event.get("attempt")
        for event in events
        if event.get("task_id") == "TASK-001" and event.get("attempt") is not None
    }
    assert attempts == {1, 2}, "по логу не восстанавливается история попыток"
