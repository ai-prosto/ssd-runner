"""BEH-42: отметка выполнения только при пройденных проверках спецификации."""

import asyncio
from pathlib import Path

from sprc_runner.config import load_config
from sprc_runner.core.task_cycle import run_attempt
from sprc_runner.core.tasks_md import TaskRecord
from sprc_runner.doubles import FakeAgent, FakeReviewer, FakeSpecChecker, FakeTestRunner
from sprc_runner.ports import ReviewVerdict

ROOT = Path(__file__).resolve().parents[2]
TASK = TaskRecord("TASK-001", "t", "p2", "todo", checklist=[("x", False)])


def _run(spec_ok, tmp_path):
    checker = FakeSpecChecker([spec_ok])
    record = asyncio.run(
        run_attempt(
            agent=FakeAgent(),
            reviewer=FakeReviewer([ReviewVerdict(True, "", "r")]),
            tests=FakeTestRunner([0]),
            spec_checker=checker,
            config=load_config(ROOT / "sprc-runner.toml"),
            task=TASK,
            number=1,
            workdir=tmp_path,
        )
    )
    return record, checker


def test_green_tests_and_review_but_failed_spec_is_not_done(tmp_path):
    record, checker = _run(False, tmp_path)
    assert checker.calls == ["TASK-001"]
    assert record.tests_returncode == 0 and record.review_passed
    assert record.success is False


def test_all_three_pass_is_done(tmp_path):
    assert _run(True, tmp_path)[0].success is True
