"""BEH-23: постоянно падающий тест завершается неуспехом за конечное число попыток."""

import asyncio
from pathlib import Path

import pytest

from sprc_runner.config import load_config
from sprc_runner.core.task_cycle import run_attempt, run_task, run_tasks
from sprc_runner.core.tasks_md import TaskRecord
from sprc_runner.doubles import FakeAgent, FakeReviewer, FakeSpecChecker, FakeTestRunner

ROOT = Path(__file__).resolve().parents[2]
TASK = TaskRecord("TASK-001", "t", "p2", "todo", checklist=[("x", False)])


class AlwaysFailing(FakeTestRunner):
    def run(self, workdir, command):
        self.calls.append(command)
        from sprc_runner.ports import TestRunResult

        return TestRunResult(command, 1, "fake://t")


def make_attempt(tmp_path, tests):
    config = load_config(ROOT / "sprc-runner.toml")
    agent = FakeAgent()

    async def attempt(task, number):
        return await run_attempt(
            agent=agent,
            reviewer=FakeReviewer(),
            tests=tests,
            spec_checker=FakeSpecChecker(),
            config=config,
            task=task,
            number=number,
            workdir=tmp_path,
        )

    return attempt


@pytest.mark.parametrize("limit", [1, 2, 5])
def test_attempts_never_exceed_limit(tmp_path, limit):
    tests = AlwaysFailing()
    result = asyncio.run(run_task(TASK, make_attempt(tmp_path, tests), limit))
    assert len(result.attempts) == limit == tests.invocations
    assert result.failed and not result.succeeded


def test_limit_comes_from_config(tmp_path):
    limit = load_config(ROOT / "sprc-runner.toml").max_attempts
    result = asyncio.run(run_task(TASK, make_attempt(tmp_path, AlwaysFailing()), limit))
    assert [a.number for a in result.attempts] == list(range(1, limit + 1))


def test_stops_at_first_success(tmp_path):
    tests = FakeTestRunner([1, 0, 1])
    result = asyncio.run(run_task(TASK, make_attempt(tmp_path, tests), 5))
    assert len(result.attempts) == 2 and result.succeeded


def test_failed_task_does_not_block_next(tmp_path):
    text = (
        "### TASK-001: A\nP2 | ⬜ TODO   Est: 1d\n\n**Checklist:**\n- [ ] c\n\n"
        "### TASK-002: B\nP2 | ⬜ TODO   Est: 1d\n\n**Checklist:**\n- [ ] c\n"
    )
    tests = FakeTestRunner([1, 1, 0])
    run = asyncio.run(run_tasks(text, make_attempt(tmp_path, tests), 2))
    assert [(r.task_id, r.succeeded) for r in run.results] == [
        ("TASK-001", False),
        ("TASK-002", True),
    ]
