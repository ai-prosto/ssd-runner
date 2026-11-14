"""Порты ядра: протоколы агента, ревьюера, git, файловой системы, тестов и часов.

Ядро работает только через эти протоколы и видит лишь простые значения,
пригодные для записи в журнал: ни типов, ни исключений, ни понятия «сообщение»
Claude Agent SDK за границу адаптера не выходит (Q-13, BEH-36).
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol


class Outcome(StrEnum):
    """Закрытый набор доменных исходов инвокации этапа (Q-10)."""

    DONE = "done"
    SUBSCRIPTION_LIMIT = "subscription_limit"
    TRANSPORT_ERROR = "transport_error"
    INVALID_CALL_CONFIG = "invalid_call_config"
    OTHER_FAILURE = "other_failure"


@dataclass(frozen=True)
class StageRequest:
    """Запрос инвокации этапа."""

    stage: str
    model: str
    inputs: tuple[str, ...]
    workdir: Path
    allowed_tools: tuple[str, ...]
    timeout_seconds: float


@dataclass(frozen=True)
class StageResult:
    """Результат инвокации этапа."""

    outcome: Outcome
    text: str
    transcript_ref: str | None
    stop_reason: str
    usage: dict[str, Any] | None = None


class AgentPort(Protocol):
    """Агент: выполнить инвокацию этапа и освободить ресурсы."""

    async def invoke(self, request: StageRequest) -> StageResult: ...

    async def close(self) -> None: ...


@dataclass(frozen=True)
class ReviewVerdict:
    """Вердикт ревью; `reviewer_id` пишется в журнал (Q-03)."""

    approved: bool
    comments: str
    reviewer_id: str


class ReviewerPort(Protocol):
    """Ревьюер: отдельный порт, реализуемый моделью или детерминированно."""

    async def review(self, request: StageRequest) -> ReviewVerdict: ...


@dataclass(frozen=True)
class DiffEntry:
    """Изменение одного файла относительно последнего коммита.

    `path` — абсолютный путь; `status` — `A` (создан), `M` (изменён), `D` (удалён);
    `lines_before` — число строк файла в последнем коммите; `added_text` — добавленные
    строки (для поиска секрета).
    """

    path: str
    status: str
    added: int = 0
    removed: int = 0
    lines_before: int = 0
    added_text: str = ""


@dataclass(frozen=True)
class DiffSummary:
    """Структурированная сводка диффа: единственный вход вердикта сторожа."""

    entries: tuple[DiffEntry, ...] = ()


class GitPort(Protocol):
    """Git: дифф, индексирование по путям, коммит, откат попытки."""

    def diff(self, workdir: Path) -> str: ...

    def diff_summary(self, workdir: Path) -> DiffSummary: ...

    def stage_paths(self, workdir: Path, paths: list[str]) -> None: ...

    def commit(self, workdir: Path, message: str) -> str: ...

    def rollback(self, workdir: Path) -> None: ...


class FilePort(Protocol):
    """Файловая система: чтение, атомарная запись, дописывание журнала."""

    def read_text(self, path: Path) -> str: ...

    def write_atomic(self, path: Path, text: str) -> None: ...

    def append_line(self, path: Path, line: str) -> None: ...


@dataclass(frozen=True)
class TestRunResult:
    """Код возврата команды тестов и ссылка на сохранённый вывод."""

    __test__ = False

    command: str
    returncode: int
    output_ref: str


class TestRunnerPort(Protocol):
    """Раннер тестов: ядро решает исход по коду возврата, вывод не разбирает."""

    __test__ = False

    def run(self, workdir: Path, command: str) -> TestRunResult: ...


@dataclass(frozen=True)
class SpecCheckResult:
    """Итог проверок спецификации и требований, относящихся к задаче (BEH-42)."""

    passed: bool
    details: str = ""


class SpecCheckerPort(Protocol):
    """Проверки из спецификации и требований задачи: часть единого критерия успеха."""

    def check(self, workdir: Path, task_id: str) -> SpecCheckResult: ...


class ClockPort(Protocol):
    """Часы: источник времени для журнала и ограничений."""

    def now(self) -> float: ...

    def monotonic(self) -> float: ...
