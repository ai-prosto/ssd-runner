"""BEH-31: исчерпание лимитов подписки — пауза с внятным сообщением (DEL-44, DEL-45)."""

import io
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
from sprc_runner.ports import Outcome, StageRequest, StageResult

TASKS = (
    "### TASK-001: Первая\nP2 | ⬜ TODO   Est: 1d\n\n**Checklist:**\n- [ ] a\n\n"
    "### TASK-002: Вторая\nP2 | ⬜ TODO   Est: 1d\n\n**Checklist:**\n- [ ] b\n\n"
    "### TASK-003: Третья\nP2 | ⬜ TODO   Est: 1d\n\n**Checklist:**\n- [ ] c\n"
)


class LimitAgent(EchoAgent):
    """Отказ по лимитам на заданном этапе (по счёту вызовов этапа)."""

    def __init__(self, stage: str | None = None, nth: int = 1) -> None:
        super().__init__()
        self.stage, self.nth, self.seen = stage, nth, 0
        self.implemented: list[str] = []

    async def invoke(self, request: StageRequest) -> StageResult:
        if request.stage == "prepare_tasks":
            self.calls.append(request)
            return StageResult(Outcome.DONE, TASKS, None, "end_turn")
        if request.stage == "implement":
            self.implemented.append(request.inputs[0].split(":")[0])
        if request.stage == self.stage:
            self.seen += 1
            if self.seen == self.nth:
                self.calls.append(request)
                return limit_result()
        return await super().invoke(request)


def _loop() -> LoopPorts:
    return LoopPorts(FakeReviewer(), FakeTestRunner(), FakeSpecChecker())


def _run(tmp_path: Path, config_file: Path, agent: LimitAgent):
    out, err = io.StringIO(), io.StringIO()
    code = main(
        ["run", "Описание.", "--config", str(config_file)],
        cwd=tmp_path,
        agent=agent,
        out=out,
        err=err,
        loop=_loop(),
    )
    run_dir = Path(out.getvalue().splitlines()[0].removeprefix("Каталог прогона: "))
    return code, run_dir, err.getvalue()


def _state(run_dir: Path) -> st.RunState:
    return st.decode_state((run_dir / "state.json").read_text(encoding="utf-8"))


def test_limit_on_implement_pauses_with_clear_message(
    tmp_path: Path, config_file: Path
) -> None:
    code, run_dir, err = _run(tmp_path, config_file, LimitAgent("implement", 2))
    assert code == 3
    assert "лимиты подписки" in err
    assert f"sprc-runner resume {run_dir}" in err
    assert "Traceback" not in err
    state = _state(run_dir)
    assert not state.finished
    assert state.reason == "implement_subscription_limit"
    assert state.tasks[0].status is st.TaskStatus.DONE
    assert state.tasks[1].attempts == 0


@pytest.mark.parametrize("stage", ["prepare_requirements", "prepare_spec"])
def test_limit_during_preparation_pauses_with_same_message(
    tmp_path: Path, config_file: Path, stage: str
) -> None:
    code, run_dir, err = _run(tmp_path, config_file, LimitAgent(stage))
    assert code == 3
    assert "лимиты подписки" in err and "Traceback" not in err
    assert not _state(run_dir).finished


def test_resume_after_limit_continues_from_next_unfinished(
    tmp_path: Path, config_file: Path
) -> None:
    _, run_dir, _ = _run(tmp_path, config_file, LimitAgent("implement", 2))
    agent, out = LimitAgent(), io.StringIO()
    code = main(
        ["resume", str(run_dir), "--config", str(config_file)],
        cwd=tmp_path,
        agent=agent,
        out=out,
        err=io.StringIO(),
        loop=_loop(),
    )
    assert code == 0
    assert "Возобновление с задачи TASK-002" in out.getvalue()
    assert agent.implemented == ["TASK-002", "TASK-003"]
    state = _state(run_dir)
    assert state.finished and state.reason == "all_succeeded"
