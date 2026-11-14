"""BEH-20: заявление модели об успехе исхода не меняет, решает код возврата."""

import asyncio
from pathlib import Path

import pytest

from sprc_runner.config import load_config
from sprc_runner.core.task_cycle import run_attempt
from sprc_runner.core.tasks_md import TaskRecord
from sprc_runner.doubles import FakeAgent, FakeReviewer, FakeSpecChecker, FakeTestRunner
from sprc_runner.ports import Outcome, StageResult

ROOT = Path(__file__).resolve().parents[2]
TASK = TaskRecord("TASK-001", "t", "p2", "todo", checklist=[("x", False)])


@pytest.mark.parametrize(
    "claim", ["TASK_COMPLETE", "все тесты зелёные, 0 failed", "tests passed"]
)
def test_claim_does_not_override_failing_tests(claim, tmp_path):
    agent = FakeAgent([StageResult(Outcome.DONE, claim, None, "end_turn")])
    record = asyncio.run(
        run_attempt(
            agent=agent,
            reviewer=FakeReviewer(),
            tests=FakeTestRunner([1]),
            spec_checker=FakeSpecChecker(),
            config=load_config(ROOT / "sprc-runner.toml"),
            task=TASK,
            number=1,
            workdir=tmp_path,
        )
    )
    assert record.tests_returncode == 1
    assert record.success is False


def test_outcome_same_for_any_claim_text(tmp_path):
    def run(text):
        return asyncio.run(
            run_attempt(
                agent=FakeAgent([StageResult(Outcome.DONE, text, None, "end_turn")]),
                reviewer=FakeReviewer(),
                tests=FakeTestRunner([0]),
                spec_checker=FakeSpecChecker(),
                config=load_config(ROOT / "sprc-runner.toml"),
                task=TASK,
                number=1,
                workdir=tmp_path,
            )
        ).success

    assert run("") is run("FAILED, tests red") is True
