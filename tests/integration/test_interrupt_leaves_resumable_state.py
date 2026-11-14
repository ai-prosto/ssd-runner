"""BEH-17: Ctrl-C оставляет читаемое консистентное состояние, годное к возобновлению."""

import io
import json
from pathlib import Path

import pytest

from sprc_runner.cli import LoopPorts, main
from sprc_runner.core import state as st
from sprc_runner.doubles import (
    EchoAgent,
    FakeReviewer,
    FakeSpecChecker,
    FakeTestRunner,
)
from sprc_runner.ports import Outcome, StageRequest, StageResult, TestRunResult

TASKS = (
    "### TASK-001: Первая\nP2 | ⬜ TODO   Est: 1d\n\n**Checklist:**\n- [ ] a\n\n"
    "### TASK-002: Вторая\nP2 | ⬜ TODO   Est: 1d\n\n**Checklist:**\n- [ ] b\n\n"
    "### TASK-003: Третья\nP2 | ⬜ TODO   Est: 1d\n\n**Checklist:**\n- [ ] c\n"
)


class TaskListAgent(EchoAgent):
    async def invoke(self, request: StageRequest) -> StageResult:
        if request.stage == "prepare_tasks":
            self.calls.append(request)
            return StageResult(Outcome.DONE, TASKS, None, "end_turn")
        return await super().invoke(request)


class InterruptingTests(FakeTestRunner):
    """Раннер тестов: на указанном по счёту запуске нажат Ctrl-C."""

    def __init__(self, interrupt_on: int, returncodes: list[int] | None = None):
        super().__init__(returncodes or [])
        self.interrupt_on = interrupt_on

    def run(self, workdir: Path, command: str) -> TestRunResult:
        if len(self.calls) + 1 == self.interrupt_on:
            self.calls.append(command)
            raise KeyboardInterrupt
        return super().run(workdir, command)


class InterruptingReviewer(FakeReviewer):
    """Ревьюер, прерываемый в середине попытки (после реализации)."""

    def __init__(self, interrupt_on: int) -> None:
        super().__init__()
        self.interrupt_on = interrupt_on

    async def review(self, request: StageRequest):
        if len(self.calls) + 1 == self.interrupt_on:
            self.calls.append(request)
            raise KeyboardInterrupt
        return await super().review(request)


def interrupted(tmp_path: Path, config_file: Path, loop: LoopPorts):
    out, err = io.StringIO(), io.StringIO()
    code = main(
        ["run", "Описание.", "--config", str(config_file)],
        cwd=tmp_path,
        agent=TaskListAgent(),
        out=out,
        err=err,
        loop=loop,
    )
    run_dir = Path(out.getvalue().splitlines()[0].removeprefix("Каталог прогона: "))
    return code, run_dir, err.getvalue()


def read_state(run_dir: Path) -> st.RunState:
    return st.decode_state((run_dir / "state.json").read_text(encoding="utf-8"))


def test_interrupt_between_tasks(tmp_path: Path, config_file: Path) -> None:
    # Ctrl-C на первом запуске тестов второй задачи: первая уже завершена
    loop = LoopPorts(FakeReviewer(), InterruptingTests(2), FakeSpecChecker())
    code, run_dir, err = interrupted(tmp_path, config_file, loop)
    assert code == 3 and "Traceback" not in err
    assert f"sprc-runner resume {run_dir}" in err
    state = read_state(run_dir)
    assert [t.status for t in state.tasks] == [
        st.TaskStatus.DONE,
        st.TaskStatus.IN_PROGRESS,
        st.TaskStatus.PENDING,
    ]
    assert not state.finished and state.reason == st.REASON_INTERRUPTED
    assert state.cursor == st.Cursor("TASK-002", 1)
    assert st.next_unfinished(state).task_id == "TASK-002"  # type: ignore[union-attr]


def test_interrupt_mid_attempt_shows_interrupted_task(
    tmp_path: Path, config_file: Path
) -> None:
    loop = LoopPorts(InterruptingReviewer(3), FakeTestRunner(), FakeSpecChecker())
    code, run_dir, _ = interrupted(tmp_path, config_file, loop)
    assert code == 3
    state = read_state(run_dir)
    assert [t.task_id for t in state.tasks if t.status is st.TaskStatus.DONE] == [
        "TASK-001",
        "TASK-002",
    ]
    assert state.cursor == st.Cursor("TASK-003", 1)
    assert state.tasks[2].status is st.TaskStatus.IN_PROGRESS
    assert state.tasks[2].attempts == 0  # прерванная попытка не засчитана


def test_interrupt_after_failed_attempt(tmp_path: Path, config_file: Path) -> None:
    # первая попытка TASK-001 неуспешна, на второй — Ctrl-C
    loop = LoopPorts(FakeReviewer(), InterruptingTests(2, [1]), FakeSpecChecker())
    code, run_dir, _ = interrupted(tmp_path, config_file, loop)
    assert code == 3
    state = read_state(run_dir)
    task = state.tasks[0]
    assert task.status is st.TaskStatus.IN_PROGRESS and task.attempts == 1
    assert task.last_attempt == st.AttemptState(1, False, "tests_failed")
    assert state.cursor == st.Cursor("TASK-001", 2)


def test_state_file_is_plain_json_and_tasks_md_marks_match(
    tmp_path: Path, config_file: Path
) -> None:
    loop = LoopPorts(FakeReviewer(), InterruptingTests(2), FakeSpecChecker())
    _, run_dir, _ = interrupted(tmp_path, config_file, loop)
    data = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == st.STATE_SCHEMA_VERSION
    tasks_md = (run_dir / "tasks.md").read_text(encoding="utf-8")
    first, second = tasks_md.split("### TASK-002")
    assert "✅ DONE" in first and "✅ DONE" not in second  # отметка уже на диске
    assert not list(run_dir.glob(".*.tmp"))


def test_interrupt_during_preparation_is_paused(
    tmp_path: Path, config_file: Path
) -> None:
    class Cancelling(EchoAgent):
        async def invoke(self, request: StageRequest) -> StageResult:
            if request.stage == "prepare_requirements":
                raise KeyboardInterrupt
            return await super().invoke(request)

    out, err = io.StringIO(), io.StringIO()
    code = main(
        ["run", "Описание.", "--config", str(config_file)],
        cwd=tmp_path,
        agent=Cancelling(),
        out=out,
        err=err,
    )
    run_dir = Path(out.getvalue().splitlines()[0].removeprefix("Каталог прогона: "))
    state = read_state(run_dir)
    assert code == 3 and state.reason == st.REASON_INTERRUPTED
    assert [s.status for s in state.stages] == [
        st.StageStatus.DONE,
        st.StageStatus.PENDING,
        st.StageStatus.PENDING,
    ]
    # подготовка не завершена — возобновление отказывает понятно, без трассировки
    code = main(
        ["resume", str(run_dir), "--config", str(config_file)],
        cwd=tmp_path,
        agent=EchoAgent(),
        out=io.StringIO(),
        err=err,
        loop=LoopPorts(FakeReviewer(), FakeTestRunner(), FakeSpecChecker()),
    )
    assert code == 4 and "Traceback" not in err.getvalue()


@pytest.mark.parametrize("breakage", ["missing", "garbage", "tampered_input"])
def test_resume_refuses_unreadable_directory(
    tmp_path: Path, config_file: Path, breakage: str
) -> None:
    loop = LoopPorts(FakeReviewer(), InterruptingTests(2), FakeSpecChecker())
    _, run_dir, _ = interrupted(tmp_path, config_file, loop)
    if breakage == "missing":
        (run_dir / "state.json").unlink()
    elif breakage == "garbage":
        (run_dir / "state.json").write_text("{oops", encoding="utf-8")
    else:
        (run_dir / "input.md").chmod(0o644)
        (run_dir / "input.md").write_text("другое", encoding="utf-8")
    err = io.StringIO()
    code = main(
        ["resume", str(run_dir), "--config", str(config_file)],
        cwd=tmp_path,
        agent=EchoAgent(),
        out=io.StringIO(),
        err=err,
        loop=LoopPorts(FakeReviewer(), FakeTestRunner(), FakeSpecChecker()),
    )
    assert code == 4 and "Возобновление невозможно" in err.getvalue()
