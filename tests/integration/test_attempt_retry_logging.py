"""BEH-22: неуспешная попытка повторяется, каждая попытка — отдельная запись."""

import asyncio
from pathlib import Path

from sprc_runner.config import load_config
from sprc_runner.core.task_cycle import run_attempt, run_task
from sprc_runner.core.tasks_md import TaskRecord
from sprc_runner.doubles import FakeAgent, FakeReviewer, FakeSpecChecker, FakeTestRunner

ROOT = Path(__file__).resolve().parents[2]
TASK = TaskRecord("TASK-001", "t", "p2", "todo", checklist=[("x", False)])


def test_each_attempt_is_separate_record_and_earlier_kept(tmp_path):
    config = load_config(ROOT / "sprc-runner.toml")
    tests = FakeTestRunner([1, 1, 0])

    async def attempt(task, number):
        return await run_attempt(
            agent=FakeAgent(),
            reviewer=FakeReviewer(),
            tests=tests,
            spec_checker=FakeSpecChecker(),
            config=config,
            task=task,
            number=number,
            workdir=tmp_path,
        )

    result = asyncio.run(run_task(TASK, attempt, 3))
    records = result.attempts
    assert [r.number for r in records] == [1, 2, 3]
    assert len({id(r) for r in records}) == 3
    assert [r.tests_returncode for r in records] == [1, 1, 0]
    assert [r.success for r in records] == [False, False, True]
    assert [r.tests_output_ref for r in records] == [
        "fake://tests/1",
        "fake://tests/2",
        "fake://tests/3",
    ]
    assert all(r.task_id == "TASK-001" for r in records)
