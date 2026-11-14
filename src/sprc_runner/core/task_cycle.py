"""Цикл задачи: порядок шагов попытки и единый критерий успеха.

Попытка идёт шагами реализация → ревью → запуск тестов (BEH-18), затем
выносятся проверки спецификации задачи. Исход решает одна функция
`task_succeeded` (BEH-19, BEH-42): ревью пройдено, код возврата запуска тестов
нулевой, проверки спецификации пройдены. Текст ответа модели в решении не
участвует (BEH-20). Цикл по задачам идёт по списку `tasks.md` (BEH-10).
"""

from collections.abc import Awaitable, Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from sprc_runner.config import RunConfig
from sprc_runner.core.checkpoint import RunCheckpoint
from sprc_runner.core.guard import commit_message, evaluate, is_product_path
from sprc_runner.core.logbook import EventType, Journal
from sprc_runner.core.progress import Progress
from sprc_runner.core.tasks_md import (
    TaskRecord,
    mark_completed,
    parse_tasks,
    validate_tasks,
)
from sprc_runner.ports import (
    AgentPort,
    FilePort,
    GitPort,
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
STEP_GUARD = "guard"
"""Шаг вердикта диффа: есть только в попытках с подключённым git (BEH-34)."""

ABORTING_OUTCOMES = frozenset(
    {Outcome.SUBSCRIPTION_LIMIT, Outcome.TRANSPORT_ERROR, Outcome.INVALID_CALL_CONFIG}
)
"""Исходы инвокации, дающие аварийную остановку прогона (Q-10, BEH-16)."""

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
    tests_command: str | None = None
    spec_checks_passed: bool = False
    success: bool = False
    model_implement: str = ""
    model_review: str = ""
    reviewer_id: str = ""
    error_code: str | None = None
    guard_violations: list[dict[str, str]] = field(default_factory=list)
    commit: str | None = None

    @property
    def aborting(self) -> bool:
        """Отказ инфраструктуры на реализации: повтор и продолжение бессмысленны."""
        return self.implement_outcome in ABORTING_OUTCOMES

    @property
    def decision(self) -> str:
        """Решение модели на шаге реализации; в критерии успеха не участвует."""
        return self.implement_outcome.value if self.implement_outcome else ""

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


def error_code(record: AttemptRecord) -> str | None:
    """Код ошибки неуспешной попытки — первая по порядку шагов причина."""
    if record.success:
        return None
    if record.implement_outcome is not Outcome.DONE:
        outcome = record.implement_outcome
        return f"implement_{outcome.value if outcome else 'missing'}"
    if not record.review_passed:
        return "review_rejected"
    if record.tests_returncode != 0:
        return "tests_failed"
    if not record.spec_checks_passed:
        return "spec_checks_failed"
    return "diff_guard_rejected"


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
    journal: Journal | None = None,
    git: GitPort | None = None,
    target: Path | None = None,
    charter_root: Path | None = None,
    files: FilePort | None = None,
    evidence_dir: Path | None = None,
) -> AttemptRecord:
    """Выполнить одну попытку: реализация → ревью → тесты → критерий успеха.

    С `journal` каждый шаг и попытка пишутся в журнал с применённой моделью,
    кодом возврата и командой тестов (BEH-04, BEH-21).
    """
    record = AttemptRecord(
        task.id,
        number,
        model_implement=config.model_implement,
        model_review=config.model_review,
    )
    ident = {"task_id": task.id, "attempt": number}
    if journal:
        journal.start(EventType.ATTEMPT_STARTED, model=config.model_implement, **ident)

    result = await agent.invoke(
        _request(STEP_IMPLEMENT, config, task, workdir, IMPLEMENT_TOOLS)
    )
    record.implement_outcome = result.outcome
    record.add(STEP_IMPLEMENT, result.outcome.value)
    if journal:
        journal.emit(
            EventType.STEP_FINISHED,
            stage=STEP_IMPLEMENT,
            model=config.model_implement,
            outcome=result.outcome.value,
            **ident,
        )

    verdict = await reviewer.review(
        _request(STEP_REVIEW, config, task, workdir, REVIEW_TOOLS)
    )
    record.review_passed = verdict.approved
    record.reviewer_id = verdict.reviewer_id
    record.add(STEP_REVIEW, "approved" if verdict.approved else "rejected")
    if journal:
        journal.emit(
            EventType.STEP_FINISHED,
            stage=STEP_REVIEW,
            model=config.model_review,
            approved=verdict.approved,
            reviewer_id=verdict.reviewer_id,
            **ident,
        )

    run = tests.run(workdir, config.test_command)
    record.tests_returncode = run.returncode
    record.tests_output_ref = run.output_ref
    record.tests_command = run.command
    record.add(STEP_TESTS, f"returncode={run.returncode}")
    if journal:
        journal.emit(
            EventType.STEP_FINISHED,
            stage=STEP_TESTS,
            returncode=run.returncode,
            command=run.command,
            output_ref=run.output_ref,
            **ident,
        )

    record.spec_checks_passed = spec_checker.check(workdir, task.id).passed
    record.success = result.outcome is Outcome.DONE and task_succeeded(
        record.review_passed, record.tests_returncode, record.spec_checks_passed
    )
    if git is not None:
        _guard_and_commit(
            record,
            task,
            workdir,
            git,
            target or workdir,
            charter_root or workdir,
            journal,
            files,
            evidence_dir,
        )
    record.error_code = error_code(record)
    if journal:
        journal.finish(
            EventType.ATTEMPT_FINISHED,
            model=config.model_implement,
            success=record.success,
            error_code=record.error_code,
            decision=record.decision,
            review_approved=record.review_passed,
            tests_returncode=run.returncode,
            tests_command=run.command,
            model_review=config.model_review,
            commit=record.commit,
            **ident,
        )
    return record


def _guard_and_commit(
    record: AttemptRecord,
    task: TaskRecord,
    workdir: Path,
    git: GitPort,
    target: Path,
    charter_root: Path,
    journal: Journal | None,
    files: FilePort | None,
    evidence_dir: Path | None,
) -> None:
    """Вердикт диффа до коммита: нарушение — без коммита, неуспех, откат."""
    summary = git.diff_summary(workdir)
    verdict = evaluate(summary, target, charter_root)
    record.guard_violations = [v.as_dict() for v in verdict.violations]
    record.add(STEP_GUARD, "allowed" if verdict.allowed else verdict.reason)
    if journal:
        journal.emit(
            EventType.DIFF_VERDICT,
            task_id=record.task_id,
            attempt=record.number,
            allowed=verdict.allowed,
            violations=record.guard_violations,
            reason=verdict.reason,
        )
    if not verdict.allowed:
        record.success = False
    if record.success:
        paths = [
            e.path
            for e in summary.entries
            if is_product_path(e.path, target, charter_root)
        ]
        git.stage_paths(workdir, paths)
        record.commit = git.commit(
            workdir,
            commit_message(
                task.id,
                record.number,
                task.name,
                run_id=journal.run_id if journal else "",
                tests_returncode=record.tests_returncode,
                review_approved=record.review_passed,
            ),
        )
        return
    if files is not None and evidence_dir is not None:
        name = f"{task.id}-attempt-{record.number}.diff"
        files.write_atomic(evidence_dir / name, git.diff(workdir))
    git.rollback(workdir)


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
    task: TaskRecord,
    attempt: AttemptFn,
    max_attempts: int,
    progress: Progress | None = None,
    first_attempt: int = 1,
    checkpoint: RunCheckpoint | None = None,
) -> TaskResult:
    """Повторять попытки до успеха, но не более `max_attempts` (BEH-22, BEH-23).

    Попытки нумеруются с 1; записи прежних попыток сохраняются в `attempts`.
    При возобновлении нумерация продолжается с `first_attempt`.
    """
    result = TaskResult(task.id)
    for number in range(first_attempt, max_attempts + 1):
        if progress:
            progress.attempt_started(task.id, number, max_attempts)
        if checkpoint:
            checkpoint.attempt_started(task.id, number)
        record = await attempt(task, number)
        result.attempts.append(record)
        if checkpoint:
            checkpoint.attempt_finished(
                task.id,
                number,
                record.success,
                record.error_code,
                record.commit,
                counted=not record.aborting,
            )
        if progress:
            progress.attempt_finished(
                task.id, number, record.success, record.error_code
            )
        if record.success or record.aborting:
            break
    return result


@dataclass
class RunResult:
    """Итог прогона списка: результаты задач и обновлённый текст `tasks.md`."""

    results: list[TaskResult]
    tasks_text: str
    aborted: bool = False
    total: int = 0

    @property
    def attempts(self) -> list[AttemptRecord]:
        return [a for r in self.results for a in r.attempts]

    @property
    def succeeded_count(self) -> int:
        return sum(1 for r in self.results if r.succeeded)


def _task_finished_fields(result: TaskResult) -> dict[str, object]:
    """Поля состава FR-06 для `task_finished`: единственный источник — попытки."""
    last = result.attempts[-1]
    return {
        "model": last.model_implement,
        "model_review": last.model_review,
        "tests_returncode": last.tests_returncode,
        "tests_command": last.tests_command,
        "review_approved": last.review_passed,
        "attempt": last.number,
        "attempts_total": len(result.attempts),
        "success": result.succeeded,
        "error_code": None if result.succeeded else last.error_code,
        "decision": last.decision,
    }


async def run_tasks(
    tasks_text: str,
    attempt: AttemptFn,
    max_attempts: int,
    journal: Journal | None = None,
    progress: Progress | None = None,
    checkpoint: RunCheckpoint | None = None,
    only: Collection[str] | None = None,
    attempts_done: Mapping[str, int] | None = None,
) -> RunResult:
    """Пройти задачи по порядку; отметка о выполнении — только после успеха (BEH-28).

    Провалившаяся задача отметки не получает, цикл переходит к следующей и
    конечен: число попыток по каждой задаче ограничено `max_attempts`.

    Возобновление: `only` ограничивает цикл незавершёнными задачами, а
    `attempts_done` — сколько попыток по задаче уже сделано до обрыва.
    """
    results: list[TaskResult] = []
    text = tasks_text
    if only is None:
        tasks = select_tasks(tasks_text)
    else:  # возобновление: список уже проверен при подготовке, пустой остаток не ошибка
        tasks = [t for t in parse_tasks(tasks_text) if t.id in only and t.is_runnable]
    aborted = False
    for task in tasks:
        if journal:
            journal.start(EventType.TASK_STARTED, task_id=task.id)
        if progress:
            progress.task_started(task.id, task.name)
        if checkpoint:
            checkpoint.task_taken(task.id)
        result = await run_task(
            task,
            attempt,
            max_attempts,
            progress,
            first_attempt=(attempts_done or {}).get(task.id, 0) + 1,
            checkpoint=checkpoint,
        )
        results.append(result)
        if journal and result.attempts:
            journal.finish(
                EventType.TASK_FINISHED,
                task_id=task.id,
                **_task_finished_fields(result),
            )
        if progress:
            progress.task_finished(task.id, result.succeeded, len(result.attempts))
        if result.succeeded:
            text = mark_completed(text, task.id)
        if result.attempts and result.attempts[-1].aborting:
            aborted = True
            if checkpoint:
                checkpoint.paused(result.attempts[-1].error_code or "aborted")
            break
        if checkpoint:
            checkpoint.task_finished(task.id, result.succeeded, text)
    return RunResult(results, text, aborted, len(tasks))


async def run_task_list(
    tasks_text: str, attempt: AttemptFn, max_attempts: int = 1
) -> list[AttemptRecord]:
    """Пройти задачи списка по порядку; вернуть записи всех попыток."""
    return (await run_tasks(tasks_text, attempt, max_attempts)).attempts


def succeeded_ids(records: Iterable[AttemptRecord]) -> list[str]:
    return [r.task_id for r in records if r.success]
