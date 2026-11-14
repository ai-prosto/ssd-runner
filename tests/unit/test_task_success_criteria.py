"""BEH-19, BEH-42: единый критерий успеха — три условия одновременно."""

import asyncio
import itertools
from pathlib import Path

import pytest

from sprc_runner.config import load_config
from sprc_runner.core.task_cycle import run_attempt, task_succeeded
from sprc_runner.core.tasks_md import TaskRecord
from sprc_runner.doubles import FakeAgent, FakeReviewer, FakeSpecChecker, FakeTestRunner
from sprc_runner.ports import Outcome, ReviewVerdict, StageResult

ROOT = Path(__file__).resolve().parents[2]
TASK = TaskRecord("TASK-001", "t", "p2", "todo", checklist=[("x", False)])


def attempt(approved, code, spec, tmp_path, agent=None):
    return asyncio.run(
        run_attempt(
            agent=agent or FakeAgent(),
            reviewer=FakeReviewer([ReviewVerdict(approved, "", "r")]),
            tests=FakeTestRunner([code]),
            spec_checker=FakeSpecChecker([spec]),
            config=load_config(ROOT / "sprc-runner.toml"),
            task=TASK,
            number=1,
            workdir=tmp_path,
        )
    )


@pytest.mark.parametrize(
    ("review", "code", "spec"),
    list(itertools.product([True, False], [0, 1], [True, False])),
)
def test_success_only_when_all_three_hold(review, code, spec, tmp_path):
    expected = review and code == 0 and spec
    assert task_succeeded(review, code, spec) is expected
    assert attempt(review, code, spec, tmp_path).success is expected


def test_tests_not_run_is_not_success():
    assert task_succeeded(True, None, True) is False


def test_failed_implementation_is_not_success(tmp_path):
    agent = FakeAgent([StageResult(Outcome.OTHER_FAILURE, "", None, "x")])
    assert attempt(True, 0, True, tmp_path, agent).success is False
