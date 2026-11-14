"""Журнал прогона `log.jsonl`: конструирование событий и поставляемый читатель.

Формат описан в `schemas/log-schema.md` (Q-08). Конструирование записей чистое;
`Journal` лишь связывает его с портами файлов и часов и ведёт порядковый номер.
Читатель (`parse_log`, `timings`) — единый парсер логов любых прогонов (BEH-26).
"""

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from sprc_runner.ports import ClockPort, FilePort

SCHEMA_VERSION = "1.0"
"""Версия схемы журнала; смена смысла или удаление поля — новая мажорная версия."""

LOG_NAME = "log.jsonl"


class EventType(StrEnum):
    """Закрытый словарь типов событий журнала."""

    RUN_STARTED = "run_started"
    RUN_FINISHED = "run_finished"
    STAGE_STARTED = "stage_started"
    STAGE_FINISHED = "stage_finished"
    TASK_STARTED = "task_started"
    ATTEMPT_STARTED = "attempt_started"
    STEP_FINISHED = "step_finished"
    DIFF_VERDICT = "diff_verdict"
    ATTEMPT_FINISHED = "attempt_finished"
    TASK_FINISHED = "task_finished"
    RUN_PAUSED = "run_paused"


class LogFormatError(ValueError):
    """Запись журнала не разбирается или имеет неподдерживаемую версию схемы."""


def build_event(
    event_type: EventType | str,
    *,
    run_id: str,
    seq: int,
    ts: float,
    stage: str | None = None,
    task_id: str | None = None,
    attempt: int | None = None,
    model: str | None = None,
    **payload: Any,
) -> dict[str, Any]:
    """Чисто построить запись с общим конвертом; пустые поля конверта опускаются."""
    kind = EventType(event_type)
    event: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "seq": seq,
        "ts": ts,
        "event": kind.value,
    }
    optional = {"stage": stage, "task_id": task_id, "attempt": attempt, "model": model}
    event.update({k: v for k, v in optional.items() if v is not None})
    event.update(payload)
    return event


def encode_event(event: dict[str, Any]) -> str:
    """Одна запись — одна строка JSON, только ASCII.

    ASCII гарантирует, что оборванная запись не режет многобайтный символ и
    читатель не падает на `UnicodeDecodeError` вместо отбрасывания хвоста.
    """
    return json.dumps(event, ensure_ascii=True, separators=(",", ":"))


class Journal:
    """Писатель журнала: дописывает событие и сбрасывает его на диск сразу."""

    def __init__(
        self, path: Path, run_id: str, files: FilePort, clock: ClockPort
    ) -> None:
        self._path = path
        self._run_id = run_id
        self._files = files
        self._clock = clock
        self._seq = 0
        self._starts: dict[tuple[str, str | None, int | None], float] = {}

    @property
    def run_id(self) -> str:
        return self._run_id

    def emit(self, event_type: EventType | str, **fields: Any) -> dict[str, Any]:
        self._seq += 1
        event = build_event(
            event_type,
            run_id=self._run_id,
            seq=self._seq,
            ts=self._clock.now(),
            **fields,
        )
        self._files.append_line(self._path, encode_event(event))
        return event

    def start(self, event_type: EventType, **fields: Any) -> dict[str, Any]:
        """Событие старта; момент запоминается для длительности завершения."""
        event = self.emit(event_type, **fields)
        self._starts[_key(event_type, fields)] = event["ts"]
        return event

    def finish(self, event_type: EventType, **fields: Any) -> dict[str, Any]:
        """Событие завершения с явными `started_at`, `finished_at`, `duration_s`."""
        started = self._starts.pop(_key(_START_OF[event_type], fields), None)
        now = self._clock.now()
        if started is None:
            started = now
        return self.emit(
            event_type,
            started_at=started,
            finished_at=now,
            duration_s=now - started,
            **fields,
        )


_START_OF = {
    EventType.RUN_FINISHED: EventType.RUN_STARTED,
    EventType.STAGE_FINISHED: EventType.STAGE_STARTED,
    EventType.TASK_FINISHED: EventType.TASK_STARTED,
    EventType.ATTEMPT_FINISHED: EventType.ATTEMPT_STARTED,
}


def _key(
    event_type: EventType, fields: dict[str, Any]
) -> tuple[str, str | None, int | None]:
    kind = event_type.value.removesuffix("_started").removesuffix("_finished")
    if event_type in (EventType.RUN_STARTED, EventType.RUN_FINISHED):
        return (kind, None, None)
    task_id = fields.get("task_id")
    attempt = fields.get("attempt")
    if kind == "stage":
        return (kind, fields.get("stage"), None)
    return (kind, task_id, attempt if kind == "attempt" else None)


def parse_log(text: str) -> list[dict[str, Any]]:
    """Разобрать журнал; оборванная последняя строка (без `\\n`) отбрасывается.

    Любая другая неразбираемая запись, неизвестный тип события или версия схемы
    с чужой мажорной частью — `LogFormatError`.
    """
    lines = text.split("\n")
    torn_tail = bool(lines[-1])
    complete = lines[:-1]
    if torn_tail:
        try:
            json.loads(lines[-1])
            complete = lines  # последняя запись целая, нет только перевода строки
        except json.JSONDecodeError:
            pass
    events: list[dict[str, Any]] = []
    for number, line in enumerate(complete, 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LogFormatError(f"строка {number}: не JSON") from exc
        events.append(_check(event, number))
    return events


def _check(event: Any, number: int) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise LogFormatError(f"строка {number}: запись не объект")
    version = event.get("schema_version")
    if not isinstance(version, str):
        raise LogFormatError(f"строка {number}: нет schema_version")
    if version.split(".")[0] != SCHEMA_VERSION.split(".")[0]:
        raise LogFormatError(f"строка {number}: неподдерживаемая схема {version}")
    if event.get("event") not in {e.value for e in EventType}:
        raise LogFormatError(f"строка {number}: неизвестное событие")
    return event


def read_log(path: Path, files: FilePort) -> list[dict[str, Any]]:
    return parse_log(files.read_text(path))


@dataclass(frozen=True)
class Timing:
    """Временные характеристики прогона или задачи по событиям журнала."""

    started_at: float
    finished_at: float
    duration_s: float


@dataclass(frozen=True)
class LogTimings:
    run: Timing | None
    tasks: dict[str, Timing]


def _timing(event: dict[str, Any]) -> Timing:
    return Timing(event["started_at"], event["finished_at"], event["duration_s"])


def timings(events: list[dict[str, Any]]) -> LogTimings:
    """Времена прогона и задач из явных полей событий завершения."""
    run: Timing | None = None
    tasks: dict[str, Timing] = {}
    for event in events:
        if event["event"] == EventType.RUN_FINISHED:
            run = _timing(event)
        elif event["event"] == EventType.TASK_FINISHED:
            tasks[event["task_id"]] = _timing(event)
    return LogTimings(run, tasks)


TASK_ROW_FIELDS = (
    "started_at",
    "task_id",
    "model",
    "tests_returncode",
    "review_approved",
    "attempt",
    "attempts_total",
    "success",
    "error_code",
    "decision",
)
"""Состав FR-06 по задаче; `error_code` обязателен только при неуспехе."""


@dataclass(frozen=True)
class TaskRow:
    """Строка проекции «события → задача»: поля состава FR-06."""

    task_id: str
    fields: dict[str, Any]

    @property
    def missing(self) -> list[str]:
        """Поля состава, которых нет в строке; любое — дефект, не допустимый пропуск."""
        absent = [
            name
            for name in TASK_ROW_FIELDS
            if name != "error_code" and self.fields.get(name) in (None, "")
        ]
        if self.fields.get("success") is False and not self.fields.get("error_code"):
            absent.append("error_code")
        return absent

    @property
    def complete(self) -> bool:
        return not self.missing


def project_tasks(events: list[dict[str, Any]]) -> list[TaskRow]:
    """Собрать по каждой завершённой задаче строку из событий журнала."""
    rows: list[TaskRow] = []
    for event in events:
        if event["event"] != EventType.TASK_FINISHED:
            continue
        fields = {name: event.get(name) for name in TASK_ROW_FIELDS}
        fields["started_at"] = event.get("started_at")
        rows.append(TaskRow(event.get("task_id") or "", fields))
    return rows


@dataclass(frozen=True)
class Completeness:
    """Полнота записи по задачам прогона относительно `tasks.md`."""

    total: int
    complete: int
    defects: dict[str, list[str]]

    @property
    def ratio(self) -> float:
        return self.complete / self.total if self.total else 1.0


def completeness(log_text: str, task_ids: list[str]) -> Completeness:
    """Внешний расчёт доли полных задач по `log.jsonl` и списку задач `tasks.md`.

    Задача без строки в журнале и строка с любым пропущенным полем — дефекты.
    """
    rows: dict[str, TaskRow] = {}
    for row in project_tasks(parse_log(log_text)):
        rows[row.task_id] = row
    defects: dict[str, list[str]] = {}
    for task_id in task_ids:
        row = rows.get(task_id)
        if row is None:
            defects[task_id] = list(TASK_ROW_FIELDS)
        elif not row.complete:
            defects[task_id] = row.missing
    return Completeness(len(task_ids), len(task_ids) - len(defects), defects)
