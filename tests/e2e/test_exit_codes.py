"""BEH-16: три исхода прогона и отказ по конфигурации — четыре разных кода."""

import io
from pathlib import Path

import pytest

from sprc_runner.cli import LoopPorts, main
from sprc_runner.core.progress import RunStatus, exit_code_for, run_status
from sprc_runner.doubles import (
    EchoAgent,
    FakeAgent,
    FakeReviewer,
    FakeSpecChecker,
    FakeTestRunner,
    limit_result,
)
from sprc_runner.ports import Outcome, StageRequest, StageResult

TASKS = (
    "### TASK-001: Первая\nP2 | ⬜ TODO   Est: 1d\n\n**Checklist:**\n- [ ] a\n\n"
    "### TASK-002: Вторая\nP2 | ⬜ TODO   Est: 1d\n\n**Checklist:**\n- [ ] b\n"
)


class TwoTaskAgent(EchoAgent):
    def __init__(self, implement: list[StageResult] | None = None) -> None:
        super().__init__()
        self.implement = list(implement or [])

    async def invoke(self, request: StageRequest) -> StageResult:
        if request.stage == "prepare_tasks":
            self.calls.append(request)
            return StageResult(Outcome.DONE, TASKS, None, "end_turn")
        if request.stage == "implement" and self.implement:
            self.calls.append(request)
            return self.implement.pop(0)
        return await super().invoke(request)


def run(tmp_path: Path, config_file: Path, agent: FakeAgent, codes: list[int]):
    err = io.StringIO()
    loop = LoopPorts(FakeReviewer(), FakeTestRunner(codes), FakeSpecChecker())
    code = main(
        ["run", "Описание.", "--config", str(config_file)],
        cwd=tmp_path,
        agent=agent,
        out=io.StringIO(),
        err=err,
        loop=loop,
    )
    return code, err.getvalue()


def test_all_succeeded(tmp_path: Path, config_file: Path) -> None:
    code, _ = run(tmp_path, config_file, TwoTaskAgent(), [])
    assert code == 0


def test_partial(tmp_path: Path, config_file: Path) -> None:
    code, _ = run(tmp_path, config_file, TwoTaskAgent(), [1, 1, 1, 0])
    assert code == 1


def test_aborted_on_limit(tmp_path: Path, config_file: Path) -> None:
    agent = TwoTaskAgent([limit_result()])
    code, err = run(tmp_path, config_file, agent, [])
    assert code == 3
    assert "Traceback" not in err
    assert [c.stage for c in agent.calls].count("implement") == 1


def test_aborted_on_prepare_failure(tmp_path: Path, config_file: Path) -> None:
    code, _ = run(tmp_path, config_file, FakeAgent([limit_result()]), [])
    assert code == 3


def test_config_refused(tmp_path: Path) -> None:
    code, _ = run(tmp_path, tmp_path / "missing.toml", FakeAgent(), [])
    assert code == 4


def test_four_codes_distinct() -> None:
    codes = {exit_code_for(s) for s in RunStatus}
    assert len(codes) == 3
    assert 4 not in codes


@pytest.mark.parametrize(
    ("done", "total", "aborted", "status"),
    [
        (2, 2, False, RunStatus.ALL_SUCCEEDED),
        (1, 2, False, RunStatus.PARTIAL),
        (0, 2, False, RunStatus.PARTIAL),
        (1, 2, True, RunStatus.ABORTED),
        (2, 2, True, RunStatus.ABORTED),
    ],
)
def test_run_status(done: int, total: int, aborted: bool, status: RunStatus) -> None:
    assert run_status(done, total, aborted) is status
