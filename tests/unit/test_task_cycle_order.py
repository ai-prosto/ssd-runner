"""BEH-18: шаги попытки идут в порядке реализация → ревью → тесты."""

import asyncio
from pathlib import Path

import pytest

from sprc_runner.config import load_config
from sprc_runner.core.task_cycle import run_attempt, step_order
from sprc_runner.core.tasks_md import TaskRecord
from sprc_runner.doubles import FakeAgent, FakeReviewer, FakeSpecChecker, FakeTestRunner
from sprc_runner.ports import ReviewVerdict

ROOT = Path(__file__).resolve().parents[2]
TASK = TaskRecord("TASK-001", "t", "p2", "todo", checklist=[("x", False)])


class Trace:
    def __init__(self) -> None:
        self.events: list[str] = []


def _run(agent, reviewer, tests, tmp_path):
    return asyncio.run(
        run_attempt(
            agent=agent,
            reviewer=reviewer,
            tests=tests,
            spec_checker=FakeSpecChecker(),
            config=load_config(ROOT / "sprc-runner.toml"),
            task=TASK,
            number=1,
            workdir=tmp_path,
        )
    )


@pytest.mark.parametrize("approved", [True, False])
@pytest.mark.parametrize("code", [0, 1])
def test_order_is_fixed_and_no_step_skipped(approved, code, tmp_path):
    trace = Trace()

    class A(FakeAgent):
        async def invoke(self, request):
            trace.events.append("implement")
            return await super().invoke(request)

    class R(FakeReviewer):
        async def review(self, request):
            trace.events.append("review")
            return await super().review(request)

    class T(FakeTestRunner):
        def run(self, workdir, command):
            trace.events.append("tests")
            return super().run(workdir, command)

    record = _run(A(), R([ReviewVerdict(approved, "", "r")]), T([code]), tmp_path)
    assert trace.events == ["implement", "review", "tests"]
    assert step_order(record) == ["implement", "review", "tests"]
    assert [s.seq for s in record.steps] == [1, 2, 3]
