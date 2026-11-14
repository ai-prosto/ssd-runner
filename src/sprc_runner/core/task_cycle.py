"""Цикл задачи: порядок шагов попытки и единый критерий успеха.

Попытка идёт шагами реализация → ревью → запуск тестов (BEH-18), затем
выносятся проверки спецификации задачи. Исход решает одна функция
`task_succeeded` (BEH-19, BEH-42): ревью пройдено, код возврата запуска тестов
нулевой, проверки спецификации пройдены. Текст ответа модели в решении не
участвует (BEH-20). Цикл по задачам идёт по списку `tasks.md` (BEH-10).
"""

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from sprc_runner.config import RunConfig
from sprc_runner.core.tasks_md import TaskRecord, mark_completed, validate_tasks
from sprc_runner.ports import (
    AgentPort,
    Outcome,
    ReviewerPort,
    SpecCheckerPort,
    StageRequest,
    TestRunnerPort,
)

STEP_IMPLEMENT = "implement"
STEP_REVIEW = "review"
STEP_TESTS = "tests"
STEPS = (STEP_IMPLEMENT, STEP_REVIEW, STEP_TESTS)

IMPLEMENT_TOOLS = ("Read", "Write", "Edit", "Bash")
REVIEW_TOOLS = ("Read",)
STEP_TIMEOUT_SECONDS = 1800.0


@dataclass(frozen=True)
class StepRecord:
    """Запись шага попытки; `seq` — порядковый номер внутри попытки."""

    seq: int
    step: str
    detail: str = ""


@dataclass
class AttemptRecord:
    """Запись попытки: шаги в порядке выполнения и факты для критерия успеха."""

    task_id: str
    number: int
    steps: list[StepRecord] = field(default_factory=list)
    implement_outcome: Outcome | None = None
    review_passed: bool = False
    tests_returncode: int | None = None
    tests_output_ref: str | None = None
    spec_checks_passed: bool = False
    success: bool = False

    def add(self, step: str, detail: str = "") -> None:
        self.steps.append(StepRecord(len(self.steps) + 1, step, detail))


def step_order(record: AttemptRecord) -> list[str]:
    """Порядок шагов, восстановленный по записям попытки."""
    return [s.step for s in sorted(record.steps, key=lambda s: s.seq)]


def task_succeeded(
    review_passed: bool, tests_returncode: int | None, spec_checks_passed: bool
) -> bool:
    """Единственный критерий успеха: все три условия одновременно.

    `tests_returncode is None` — тесты не запускались, это не успех.
    """
    return review_passed and tests_returncode == 0 and spec_checks_passed


def select_tasks(tasks_text: str) -> list[TaskRecord]:
    """Задачи цикла: пригодные записи списка `tasks.md` в порядке файла (BEH-10)."""
    return [t for t in validate_tasks(tasks_text) if t.is_runnable]


def _request(
    stage: str,
    config: RunConfig,
    task: TaskRecord,
    workdir: Path,
    tools: tuple[str, ...],
) -> StageRequest:
    checklist = "\n".join(f"- {text}" for text, _ in task.checklist)
    return StageRequest(
        stage=stage,
        model=config.model_review if stage == STEP_REVIEW else config.model_implement,
        inputs=(f"{task.id}: {task.name}", checklist),
        workdir=workdir,
        allowed_tools=tools,
        timeout_seconds=STEP_TIMEOUT_SECONDS,
    )


async def run_attempt(
    *,
    agent: AgentPort,
    reviewer: ReviewerPort,
    tests: TestRunnerPort,
    spec_checker: SpecCheckerPort,
    config: RunConfig,
    task: TaskRecord,
    number: int,
    workdir: Path,
) -> AttemptRecord:
    """Выполнить одну попытку: реализация → ревью → тесты → критерий успеха."""
    record = AttemptRecord(task.id, number)

    result = await agent.invoke(
        _request(STEP_IMPLEMENT, config, task, workdir, IMPLEMENT_TOOLS)
    )
    record.implement_outcome = result.outcome
    record.add(STEP_IMPLEMENT, result.outcome.value)

    verdict = await reviewer.review(
        _request(STEP_REVIEW, config, task, workdir, REVIEW_TOOLS)
    )
    record.review_passed = verdict.approved
    record.add(STEP_REVIEW, "approved" if verdict.approved else "rejected")

    run = tests.run(workdir, config.test_command)
    record.tests_returncode = run.returncode
    record.tests_output_ref = run.output_ref
    record.add(STEP_TESTS, f"returncode={run.returncode}")

    record.spec_checks_passed = spec_checker.check(workdir, task.id).passed
    record.success = result.outcome is Outcome.DONE and task_succeeded(
        record.review_passed, record.tests_returncode, record.spec_checks_passed
    )
    return record


AttemptFn = Callable[[TaskRecord, int], Awaitable[AttemptRecord]]


@dataclass
class TaskResult:
    """Итог задачи: все попытки (каждая — своя запись) и признак успеха."""

    task_id: str
    attempts: list[AttemptRecord] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return bool(self.attempts) and self.attempts[-1].success

    @property
    def failed(self) -> bool:
        """Провалена: попытки исчерпаны без успеха."""
        return not self.succeeded


async def run_task(
    task: TaskRecord, attempt: AttemptFn, max_attempts: int
) -> TaskResult:
    """Повторять попытки до успеха, но не более `max_attempts` (BEH-22, BEH-23).

    Попытки нумеруются с 1; записи прежних попыток сохраняются в `attempts`.
    """
    result = TaskResult(task.id)
    for number in range(1, max_attempts + 1):
        record = await attempt(task, number)
        result.attempts.append(record)
        if record.success:
            break
    return result


@dataclass
class RunResult:
    """Итог прогона списка: результаты задач и обновлённый текст `tasks.md`."""

    results: list[TaskResult]
    tasks_text: str

    @property
    def attempts(self) -> list[AttemptRecord]:
        return [a for r in self.results for a in r.attempts]


async def run_tasks(
    tasks_text: str, attempt: AttemptFn, max_attempts: int
) -> RunResult:
    """Пройти задачи по порядку; отметка о выполнении — только после успеха (BEH-28).

    Провалившаяся задача отметки не получает, цикл переходит к следующей и
    конечен: число попыток по каждой задаче ограничено `max_attempts`.
    """
    results: list[TaskResult] = []
    text = tasks_text
    for task in select_tasks(tasks_text):
        result = await run_task(task, attempt, max_attempts)
        results.append(result)
        if result.succeeded:
            text = mark_completed(text, task.id)
    return RunResult(results, text)


async def run_task_list(
    tasks_text: str, attempt: AttemptFn, max_attempts: int = 1
) -> list[AttemptRecord]:
    """Пройти задачи списка по порядку; вернуть записи всех попыток."""
    return (await run_tasks(tasks_text, attempt, max_attempts)).attempts


def succeeded_ids(records: Iterable[AttemptRecord]) -> list[str]:
    return [r.task_id for r in records if r.success]
