"""Подготовка прогона: фиксация входного описания и три артефакта подготовки.

Два этапа конфигурации — подготовка спеки и требований (`model_prepare`),
составление списка задач (`model_tasks`) — дают три артефакта; каждый
следующий получает на вход предыдущий. Модель берётся из конфигурации
и применяется именно к своему этапу, ничего не подбирается (BEH-01).
"""

import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from sprc_runner.config import RunConfig
from sprc_runner.core.checkpoint import RunCheckpoint
from sprc_runner.core.layout import SERVICE_DIR_NAME, gitignore_entry, run_dir_path
from sprc_runner.core.logbook import EventType, Journal
from sprc_runner.core.progress import Progress
from sprc_runner.core.tasks_md import TasksFormatError, validate_tasks
from sprc_runner.ports import AgentPort, Outcome, StageRequest, StageResult

INPUT_NAME = "input.md"
SPEC_NAME = "spec.md"
REQUIREMENTS_NAME = "requirements.md"
TASKS_NAME = "tasks.md"

STAGE_NAMES = ("prepare_spec", "prepare_requirements", "prepare_tasks")
"""Этапы подготовки в порядке выполнения; их статусы ведёт состояние прогона."""

PREPARE_TIMEOUT_SECONDS = 1800.0
PREPARE_TOOLS = ("Read",)
"""Подготовительные этапы только читают; артефакты пишет раннер в каталог прогона."""

RESPONSE_CONTRACT = (
    "Ответь только текстом артефакта: без пояснений, без преамбул и без "
    "обёрток ``` — твой ответ дословно станет содержимым файла. Файл записывает "
    "раннер, инструментов записи у тебя нет и попытки записи будут отклонены."
)
"""Серия 2026-11-14: 6 из 10 прогонов упали, потому что агент пытался писать
файлы сам и возвращал пояснение с артефактом в ```-обёртке."""

SPEC_INSTRUCTION = "Составь спецификацию по входному описанию. " + RESPONSE_CONTRACT
REQUIREMENTS_INSTRUCTION = (
    "Составь требования по входному описанию и спецификации. " + RESPONSE_CONTRACT
)
TASKS_TEST_OBLIGATION = (
    "Каждая задача списка, включая первую (каркас), обязана добавлять или менять "
    "автотесты сделанного: после её выполнения pytest должен собрать и пройти "
    "хотя бы один тест. Задача, после которой pytest не собирает ни одного теста, "
    "будет провалена по коду возврата — не планируй таких задач."
)
"""Живой прогон 2026-11-14: каркасная задача без тестов падает на pytest exit 5."""

TASKS_INSTRUCTION = (
    "Составь список задач по спецификации и требованиям. "
    + TASKS_TEST_OBLIGATION
    + " "
    + RESPONSE_CONTRACT
)


def with_format_source(
    instruction: str, config: RunConfig, body: str | None = None
) -> str:
    """Дополнить текст инструкции источником формата из конфигурации (DEL-47).

    Меняется только текст первого входа; число и порядок входов инвокации прежние.
    С `body` содержимое источника приводится прямо в инструкции: рабочий каталог
    агента — каталог прогона, и файл по относительной ссылке оттуда не прочитать
    (живой прогон 2026-11-14 вернул артефакты-отказы, помеченные done).
    """
    head = (
        f"{instruction}\nФормат артефакта — по источнику "
        f"{config.format_skill_path} версии {config.format_skill_version}."
    )
    if body is None:
        return head
    return f"{head} Содержимое источника — единственный эталон структуры:\n\n{body}"


def format_source_body(config: RunConfig, format_root: Path) -> str:
    """Содержимое источника формата; его отсутствие — отказ до первой инвокации."""
    path = format_root / config.format_skill_path
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PrepareError(
            "prepare_spec",
            StageResult(
                Outcome.OTHER_FAILURE,
                "",
                None,
                f"источник формата недоступен: {path}: {exc.strerror or exc}",
            ),
        ) from exc


def _ensure_structured(
    stage: str, model: str, text: str, checkpoint: "RunCheckpoint | None"
) -> None:
    """Текст без единого заголовка — отказ модели, а не артефакт подготовки.

    Слова модели не превращают отказ в артефакт (та же дисциплина, что BEH-20
    для цикла задач): этап помечается неуспешным, текст остаётся на диске
    как эвиденция.
    """
    if any(line.lstrip().startswith("#") for line in text.splitlines()):
        return
    if checkpoint:
        checkpoint.stage_finished(stage, model, False)
    raise PrepareError(
        stage,
        StageResult(
            Outcome.OTHER_FAILURE,
            text,
            None,
            f"{stage}: в артефакте нет ни одного заголовка — "
            "текст отказа вместо артефакта",
        ),
    )


@dataclass(frozen=True)
class PrepareArtifacts:
    """Пути артефактов подготовки в каталоге прогона."""

    spec: Path
    requirements: Path
    tasks: Path


class PrepareError(Exception):
    """Этап подготовки завершился не успехом; `result` — его доменный исход."""

    def __init__(self, stage: str, result: StageResult) -> None:
        self.stage = stage
        self.result = result
        super().__init__(f"этап {stage}: {result.outcome} ({result.stop_reason})")


def new_run_id(now: float | None = None) -> str:
    """Идентификатор прогона: время UTC и случайный суффикс."""
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now))
    return f"{stamp}-{secrets.token_hex(3)}"


def ensure_gitignore(target: Path, name: str = SERVICE_DIR_NAME) -> None:
    """Создать или дополнить `.gitignore` в `<target>` строкой `.sprc/`."""
    path = target / ".gitignore"
    entry = gitignore_entry(name)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if entry in (line.strip() for line in existing.splitlines()):
        return
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    path.write_text(f"{existing}{prefix}{entry}\n", encoding="utf-8")


def init_run(
    target: Path, run_id: str, description: bytes, name: str = SERVICE_DIR_NAME
) -> Path:
    """Инициализировать прогон: каталог, `.gitignore`, копия описания байт в байт.

    Копия делается первым действием и больше не трогается (BEH-12).
    """
    target.mkdir(parents=True, exist_ok=True)
    ensure_gitignore(target, name)
    run_dir = run_dir_path(target, run_id, name)
    run_dir.mkdir(parents=True, exist_ok=False)
    copy = run_dir / INPUT_NAME
    copy.write_bytes(description)
    os.chmod(copy, 0o444)
    return run_dir


async def _invoke(
    agent: AgentPort,
    run_dir: Path,
    stage: str,
    model: str,
    inputs: tuple[str, ...],
    journal: Journal | None = None,
    progress: Progress | None = None,
    checkpoint: RunCheckpoint | None = None,
) -> str:
    if progress:
        progress.stage(stage)
    if journal:
        journal.start(EventType.STAGE_STARTED, stage=stage, model=model)
    result = await agent.invoke(
        StageRequest(
            stage=stage,
            model=model,
            inputs=inputs,
            workdir=run_dir,
            allowed_tools=PREPARE_TOOLS,
            timeout_seconds=PREPARE_TIMEOUT_SECONDS,
        )
    )
    if journal:
        journal.finish(
            EventType.STAGE_FINISHED,
            stage=stage,
            model=model,
            outcome=result.outcome.value,
        )
    if checkpoint:
        checkpoint.stage_finished(stage, model, result.outcome is Outcome.DONE)
    if result.outcome is not Outcome.DONE:
        raise PrepareError(stage, result)
    return result.text


async def run_preparation(
    agent: AgentPort,
    config: RunConfig,
    run_dir: Path,
    journal: Journal | None = None,
    progress: Progress | None = None,
    checkpoint: RunCheckpoint | None = None,
    format_root: Path | None = None,
) -> PrepareArtifacts:
    """Выполнить подготовку: спека → требования → список задач.

    С `format_root` содержимое источника формата читается до первой инвокации
    и включается в инструкции этапов; без него остаётся только ссылка.
    """
    body = None if format_root is None else format_source_body(config, format_root)
    description = (run_dir / INPUT_NAME).read_bytes().decode("utf-8", errors="replace")
    spec = await _invoke(
        agent,
        run_dir,
        "prepare_spec",
        config.model_prepare,
        (with_format_source(SPEC_INSTRUCTION, config, body), description),
        journal,
        progress,
        checkpoint,
    )
    paths = PrepareArtifacts(
        run_dir / SPEC_NAME, run_dir / REQUIREMENTS_NAME, run_dir / TASKS_NAME
    )
    paths.spec.write_text(spec, encoding="utf-8")
    _ensure_structured("prepare_spec", config.model_prepare, spec, checkpoint)
    requirements = await _invoke(
        agent,
        run_dir,
        "prepare_requirements",
        config.model_prepare,
        (with_format_source(REQUIREMENTS_INSTRUCTION, config, body), description, spec),
        journal,
        progress,
        checkpoint,
    )
    paths.requirements.write_text(requirements, encoding="utf-8")
    _ensure_structured(
        "prepare_requirements", config.model_prepare, requirements, checkpoint
    )
    tasks = await _invoke(
        agent,
        run_dir,
        "prepare_tasks",
        config.model_tasks,
        (with_format_source(TASKS_INSTRUCTION, config, body), spec, requirements),
        journal,
        progress,
        checkpoint,
    )
    paths.tasks.write_text(tasks, encoding="utf-8")
    try:
        validate_tasks(tasks)
    except TasksFormatError as exc:
        if checkpoint:
            checkpoint.stage_finished("prepare_tasks", config.model_tasks, False)
        raise PrepareError(
            "prepare_tasks",
            StageResult(Outcome.OTHER_FAILURE, tasks, None, f"tasks.md: {exc}"),
        ) from exc
    return paths
