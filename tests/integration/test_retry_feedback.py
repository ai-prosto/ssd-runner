"""Регрессия серии 3 (2026-11-15): повторная попытка получает причину провала.

Без фидбэка ретрай слепо повторял первую попытку: рабочий каталог откатывается
к последнему коммиту, а вход реализации не менялся между попытками.
"""

from pathlib import Path

from sprc_runner.adapters.fs import LocalFiles
from sprc_runner.cli import LoopPorts, main
from sprc_runner.core.task_cycle import AttemptRecord, retry_feedback
from sprc_runner.doubles import (
    EchoAgent,
    FakeReviewer,
    FakeSpecChecker,
    FakeTestRunner,
)
from sprc_runner.ports import ReviewVerdict

REJECTION = "нет обработки деления на ноль, добавь тест на неё"


def test_second_attempt_receives_review_rejection(
    tmp_path: Path, config_file: Path
) -> None:
    agent = EchoAgent()
    reviewer = FakeReviewer([ReviewVerdict(False, REJECTION, "r1")])
    code = main(
        ["run", "описание", "--config", str(config_file)],
        cwd=tmp_path,
        agent=agent,
        loop=LoopPorts(reviewer, FakeTestRunner(), FakeSpecChecker()),
    )
    assert code == 0  # вторая попытка принята
    implements = [c for c in agent.calls if c.stage == "implement"]
    assert len(implements) == 2
    assert all(REJECTION not in text for text in implements[0].inputs)
    feedback = implements[1].inputs[-1]
    assert "Попытка 1" in feedback and REJECTION in feedback


def test_feedback_carries_tests_output_tail(tmp_path: Path) -> None:
    log = tmp_path / "tests-0001.log"
    log.write_text("....\nFAILED test_div - ZeroDivisionError\n", encoding="utf-8")
    previous = AttemptRecord(
        "TASK-001",
        1,
        review_passed=True,
        spec_checks_passed=True,
        tests_returncode=1,
        tests_output_ref=str(log),
    )
    feedback = retry_feedback(previous, LocalFiles())
    assert feedback is not None
    assert "кодом 1" in feedback and "ZeroDivisionError" in feedback


def test_no_feedback_after_success_or_first_attempt() -> None:
    assert retry_feedback(None) is None
    done = AttemptRecord("TASK-001", 1, success=True)
    assert retry_feedback(done) is None
