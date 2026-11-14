"""BEH-30: `resume` продолжает со следующей невыполненной задачи и доходит до конца."""

import io
from dataclasses import replace
from pathlib import Path

import pytest

from sprc_runner.cli import LoopPorts, main
from sprc_runner.core import state as st
from sprc_runner.doubles import (
    EchoAgent,
    FakeReviewer,
    FakeSpecChecker,
    FakeTestRunner,
    limit_result,
)
from sprc_runner.ports import Outcome, StageRequest, StageResult, TestRunResult

TASKS = (
    "### TASK-001: Первая\nP2 | ⬜ TODO   Est: 1d\n\n**Checklist:**\n- [ ] a\n\n"
    "### TASK-002: Вторая\nP2 | ⬜ TODO   Est: 1d\n\n**Checklist:**\n- [ ] b\n\n"
    "### TASK-003: Третья\nP2 | ⬜ TODO   Est: 1d\n\n**Checklist:**\n- [ ] c\n"
)
ARTIFACTS = ("input.md", "spec.md", "requirements.md", "tasks.md")


class TaskListAgent(EchoAgent):
    """Агент; в `implement` фиксирует, по какой задаче его позвали."""

    def __init__(self) -> None:
        super().__init__()
        self.implemented: list[str] = []
        self.limit_on_implement: int | None = None

    async def invoke(self, request: StageRequest) -> StageResult:
        if request.stage == "prepare_tasks":
            self.calls.append(request)
            return StageResult(Outcome.DONE, TASKS, None, "end_turn")
        if request.stage == "implement":
            self.implemented.append(request.inputs[0].split(":")[0])
            if self.limit_on_implement == len(self.implemented):
                self.calls.append(request)
                return limit_result()
        return await super().invoke(request)


class InterruptingTests(FakeTestRunner):
    def __init__(self, interrupt_on: int, returncodes: list[int] | None = None):
        super().__init__(returncodes or [])
        self.interrupt_on = interrupt_on

    def run(self, workdir: Path, command: str) -> TestRunResult:
        if len(self.calls) + 1 == self.interrupt_on:
            self.calls.append(command)
            raise KeyboardInterrupt
        return super().run(workdir, command)


def first_run(tmp_path: Path, config_file: Path, tests, agent=None):
    out = io.StringIO()
    agent = agent or TaskListAgent()
    code = main(
        ["run", "Описание.", "--config", str(config_file)],
        cwd=tmp_path,
        agent=agent,
        out=out,
        err=io.StringIO(),
        loop=LoopPorts(FakeReviewer(), tests, FakeSpecChecker()),
    )
    run_dir = Path(out.getvalue().splitlines()[0].removeprefix("Каталог прогона: "))
    return code, run_dir


def resume(tmp_path: Path, config_file: Path, run_dir: Path, tests=None):
    agent, out = TaskListAgent(), io.StringIO()
    code = main(
        ["resume", str(run_dir), "--config", str(config_file)],
        cwd=tmp_path,
        agent=agent,
        out=out,
        err=io.StringIO(),
        loop=LoopPorts(FakeReviewer(), tests or FakeTestRunner(), FakeSpecChecker()),
    )
    return code, agent, out.getvalue()


def snapshot(run_dir: Path) -> dict[str, tuple[bytes, int]]:
    return {
        n: ((run_dir / n).read_bytes(), (run_dir / n).stat().st_mtime_ns)
        for n in ARTIFACTS
        if n != "tasks.md"
    }


def state_of(run_dir: Path) -> st.RunState:
    return st.decode_state((run_dir / "state.json").read_text(encoding="utf-8"))


def test_resume_after_break_between_tasks(tmp_path: Path, config_file: Path) -> None:
    code, run_dir = first_run(tmp_path, config_file, InterruptingTests(3))
    assert code == 3
    before = snapshot(run_dir)
    tasks_before = (run_dir / "tasks.md").read_text(encoding="utf-8")
    assert tasks_before.count("✅ DONE") == 2

    code, agent, out = resume(tmp_path, config_file, run_dir)

    assert code == 0
    assert "Возобновление с задачи TASK-003" in out
    # ни одной инвокации по завершённым задачам
    assert agent.implemented == ["TASK-003"]
    # артефакты не тронуты, отметки завершённых задач остались
    assert snapshot(run_dir) == before
    tasks_after = (run_dir / "tasks.md").read_text(encoding="utf-8")
    head = tasks_after.split("### TASK-003")[0]
    assert head == tasks_before.split("### TASK-003")[0]
    assert tasks_after.count("✅ DONE") == 3
    state = state_of(run_dir)
    assert state.finished and state.reason == "all_succeeded"
    assert [t.status for t in state.tasks] == [st.TaskStatus.DONE] * 3


def test_resume_after_break_mid_attempt_restarts_that_attempt(
    tmp_path: Path, config_file: Path
) -> None:
    code, run_dir = first_run(tmp_path, config_file, InterruptingTests(2))
    assert code == 3
    code, agent, _ = resume(tmp_path, config_file, run_dir)
    assert code == 0
    assert agent.implemented == ["TASK-002", "TASK-003"]
    assert state_of(run_dir).tasks[1].attempts == 1  # прерванная попытка — заново


def test_resume_after_failed_attempt_continues_attempt_numbering(
    tmp_path: Path, config_file: Path
) -> None:
    # TASK-001: попытка 1 неуспешна, на попытке 2 обрыв
    code, run_dir = first_run(tmp_path, config_file, InterruptingTests(2, [1]))
    assert code == 3
    code, agent, out = resume(tmp_path, config_file, run_dir)
    assert code == 0
    assert "[попытка] TASK-001: 2/3" in out
    assert "[попытка] TASK-001: 1/3" not in out
    assert state_of(run_dir).tasks[0].attempts == 2


def test_resume_does_not_retry_failed_tasks_and_reports_partial(
    tmp_path: Path, config_file: Path
) -> None:
    # TASK-001 исчерпала три попытки; на TASK-002 обрыв
    code, run_dir = first_run(tmp_path, config_file, InterruptingTests(4, [1, 1, 1]))
    assert code == 3
    assert state_of(run_dir).tasks[0].status is st.TaskStatus.FAILED
    code, agent, _ = resume(tmp_path, config_file, run_dir)
    assert code == 1  # дошли до конца, есть неуспешная задача
    assert agent.implemented == ["TASK-002", "TASK-003"]


def test_resume_after_subscription_limit(tmp_path: Path, config_file: Path) -> None:
    agent = TaskListAgent()
    agent.limit_on_implement = 2
    code, run_dir = first_run(tmp_path, config_file, FakeTestRunner(), agent)
    assert code == 3
    state = state_of(run_dir)
    assert not state.finished and state.tasks[1].status is st.TaskStatus.IN_PROGRESS
    assert state.tasks[1].attempts == 0  # отказ инфраструктуры не засчитан
    code, agent, _ = resume(tmp_path, config_file, run_dir)
    assert code == 0 and agent.implemented == ["TASK-002", "TASK-003"]


def test_resume_repairs_break_between_mark_and_state(
    tmp_path: Path, config_file: Path
) -> None:
    code, run_dir = first_run(tmp_path, config_file, InterruptingTests(3))
    assert code == 3
    # имитация: отметка TASK-002 на диске есть, а состояние ещё не записано
    state = state_of(run_dir)
    broken = st.take_task(
        st.start_attempt(
            replace(state, tasks=_reset(state, "TASK-002")),
            "TASK-002",
            1,
        ),
        "TASK-002",
    )
    (run_dir / "state.json").write_text(st.encode_state(broken), encoding="utf-8")
    code, agent, _ = resume(tmp_path, config_file, run_dir)
    assert code == 0 and agent.implemented == ["TASK-003"]


def _reset(state: st.RunState, task_id: str) -> tuple[st.TaskState, ...]:
    return tuple(
        st.TaskState(t.task_id) if t.task_id == task_id else t for t in state.tasks
    )


def test_resume_of_finished_run_is_noop(tmp_path: Path, config_file: Path) -> None:
    code, run_dir = first_run(tmp_path, config_file, FakeTestRunner())
    assert code == 0
    code, agent, _ = resume(tmp_path, config_file, run_dir)
    assert code == 0 and agent.implemented == []


def test_resume_requires_loop_ports(tmp_path: Path, config_file: Path) -> None:
    _, run_dir = first_run(tmp_path, config_file, InterruptingTests(2))
    err = io.StringIO()
    code = main(
        ["resume", str(run_dir), "--config", str(config_file)],
        cwd=tmp_path,
        agent=EchoAgent(),
        out=io.StringIO(),
        err=err,
    )
    assert code == 4 and "порты" in err.getvalue()


@pytest.mark.parametrize("relative", [True, False])
def test_resume_accepts_relative_and_absolute_run_dir(
    tmp_path: Path, config_file: Path, relative: bool
) -> None:
    _, run_dir = first_run(tmp_path, config_file, InterruptingTests(2))
    arg = str(run_dir.relative_to(tmp_path)) if relative else str(run_dir)
    out = io.StringIO()
    code = main(
        ["resume", arg, "--config", str(config_file)],
        cwd=tmp_path,
        agent=TaskListAgent(),
        out=out,
        err=io.StringIO(),
        loop=LoopPorts(FakeReviewer(), FakeTestRunner(), FakeSpecChecker()),
    )
    assert code == 0
