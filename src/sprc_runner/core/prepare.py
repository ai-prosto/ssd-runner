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
from sprc_runner.core.layout import SERVICE_DIR_NAME, gitignore_entry, run_dir_path
from sprc_runner.core.logbook import EventType, Journal
from sprc_runner.core.tasks_md import TasksFormatError, validate_tasks
from sprc_runner.ports import AgentPort, Outcome, StageRequest, StageResult

INPUT_NAME = "input.md"
SPEC_NAME = "spec.md"
REQUIREMENTS_NAME = "requirements.md"
TASKS_NAME = "tasks.md"

PREPARE_TIMEOUT_SECONDS = 1800.0
PREPARE_TOOLS = ("Read",)
"""Подготовительные этапы только читают; артефакты пишет раннер в каталог прогона."""

SPEC_INSTRUCTION = "Составь спецификацию по входному описанию."
REQUIREMENTS_INSTRUCTION = "Составь требования по входному описанию и спецификации."
TASKS_INSTRUCTION = "Составь список задач по спецификации и требованиям."


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
) -> str:
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
    if result.outcome is not Outcome.DONE:
        raise PrepareError(stage, result)
    return result.text


async def run_preparation(
    agent: AgentPort,
    config: RunConfig,
    run_dir: Path,
    journal: Journal | None = None,
) -> PrepareArtifacts:
    """Выполнить подготовку: спека → требования → список задач."""
    description = (run_dir / INPUT_NAME).read_bytes().decode("utf-8", errors="replace")
    spec = await _invoke(
        agent,
        run_dir,
        "prepare_spec",
        config.model_prepare,
        (SPEC_INSTRUCTION, description),
        journal,
    )
    paths = PrepareArtifacts(
        run_dir / SPEC_NAME, run_dir / REQUIREMENTS_NAME, run_dir / TASKS_NAME
    )
    paths.spec.write_text(spec, encoding="utf-8")
    requirements = await _invoke(
        agent,
        run_dir,
        "prepare_requirements",
        config.model_prepare,
        (REQUIREMENTS_INSTRUCTION, description, spec),
        journal,
    )
    paths.requirements.write_text(requirements, encoding="utf-8")
    tasks = await _invoke(
        agent,
        run_dir,
        "prepare_tasks",
        config.model_tasks,
        (TASKS_INSTRUCTION, spec, requirements),
        journal,
    )
    paths.tasks.write_text(tasks, encoding="utf-8")
    try:
        validate_tasks(tasks)
    except TasksFormatError as exc:
        raise PrepareError(
            "prepare_tasks",
            StageResult(Outcome.OTHER_FAILURE, tasks, None, f"tasks.md: {exc}"),
        ) from exc
    return paths
