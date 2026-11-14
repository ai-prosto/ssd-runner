"""Состояние прогона `state.json`: схема и чистые переходы (Q-07, BEH-29, BEH-30).

Состояние отвечает на вопрос «что делать дальше»; журнал `log.jsonl` — на вопрос
«что происходило», при возобновлении журнал не читается. Все переходы чистые:
берут состояние и возвращают новое; запись на диск — дело `core/checkpoint.py`.
Формат описан в `schemas/state-schema.md`.
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

STATE_SCHEMA_VERSION = "1.0"
"""Версия схемы состояния; смена смысла или удаление поля — новая мажорная версия."""

STATE_NAME = "state.json"

REASON_INTERRUPTED = "interrupted"
"""Код причины паузы при прерывании пользователем."""


class StateFormatError(ValueError):
    """Документ состояния не разбирается или не соответствует схеме."""


class TaskStatus(StrEnum):
    """Закрытый набор статусов задачи."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"


class StageStatus(StrEnum):
    """Статус этапа подготовки."""

    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"


UNFINISHED = frozenset({TaskStatus.PENDING, TaskStatus.IN_PROGRESS})
"""Статусы незавершённой задачи: именно их подхватывает возобновление."""


@dataclass(frozen=True)
class StageState:
    """Этап подготовки: статус и применённая модель (пока этап не шёл — `None`)."""

    name: str
    status: StageStatus = StageStatus.PENDING
    model: str | None = None


@dataclass(frozen=True)
class AttemptState:
    """Описание последней попытки задачи."""

    number: int
    success: bool
    error_code: str | None = None


@dataclass(frozen=True)
class TaskState:
    """Задача: статус, число сделанных попыток, последняя попытка, коммит."""

    task_id: str
    status: TaskStatus = TaskStatus.PENDING
    attempts: int = 0
    last_attempt: AttemptState | None = None
    commit: str | None = None


@dataclass(frozen=True)
class Cursor:
    """Курсор: какая задача в работе и какая попытка идёт (если идёт)."""

    task_id: str
    attempt: int | None = None


@dataclass(frozen=True)
class RunState:
    """Документ состояния прогона."""

    run_id: str
    input_sha256: str
    config: dict[str, Any]
    stages: tuple[StageState, ...] = ()
    tasks: tuple[TaskState, ...] = ()
    cursor: Cursor | None = None
    finished: bool = False
    reason: str | None = None
    schema_version: str = field(default=STATE_SCHEMA_VERSION)


def new_state(
    run_id: str,
    input_sha256: str,
    config: dict[str, Any],
    stages: Iterable[str],
) -> RunState:
    """Состояние при старте прогона: этапы ждут, задач ещё нет."""
    return RunState(
        run_id, input_sha256, dict(config), tuple(StageState(n) for n in stages)
    )


def finish_stage(state: RunState, stage: str, model: str, ok: bool) -> RunState:
    """Этап завершён: статус и применённая модель."""
    status = StageStatus.DONE if ok else StageStatus.FAILED
    updated = tuple(
        replace(s, status=status, model=model) if s.name == stage else s
        for s in state.stages
    )
    if all(s.name != stage for s in state.stages):
        updated = (*updated, StageState(stage, status, model))
    return replace(state, stages=updated)


def register_tasks(state: RunState, task_ids: Iterable[str]) -> RunState:
    """Занести список задач после составления `tasks.md`; все ждут своей очереди."""
    return replace(state, tasks=tuple(TaskState(t) for t in task_ids))


def _update_task(state: RunState, task_id: str, **changes: Any) -> RunState:
    if all(t.task_id != task_id for t in state.tasks):
        raise StateFormatError(f"задачи {task_id} нет в состоянии")
    tasks = tuple(
        replace(t, **changes) if t.task_id == task_id else t for t in state.tasks
    )
    return replace(state, tasks=tasks)


def take_task(state: RunState, task_id: str) -> RunState:
    """Задача взята в работу: статус `in_progress`, курсор на ней."""
    state = _update_task(state, task_id, status=TaskStatus.IN_PROGRESS)
    return replace(state, cursor=Cursor(task_id))


def start_attempt(state: RunState, task_id: str, number: int) -> RunState:
    """Попытка началась: по курсору видно, какая задача прервана, если оборвётся."""
    state = _update_task(state, task_id, status=TaskStatus.IN_PROGRESS)
    return replace(state, cursor=Cursor(task_id, number))


def finish_attempt(
    state: RunState,
    task_id: str,
    number: int,
    success: bool,
    error_code: str | None = None,
    commit: str | None = None,
    counted: bool = True,
) -> RunState:
    """Попытка завершена; `counted=False` — отказ инфраструктуры, попытка не считается.

    Ссылка на коммит обновляется только когда он есть: коммит прежней успешной
    попытки не затирается.
    """
    task = next((t for t in state.tasks if t.task_id == task_id), None)
    if task is None:
        raise StateFormatError(f"задачи {task_id} нет в состоянии")
    changes: dict[str, Any] = {
        "attempts": max(task.attempts, number) if counted else task.attempts,
        "last_attempt": AttemptState(number, success, error_code),
    }
    if commit is not None:
        changes["commit"] = commit
    state = _update_task(state, task_id, **changes)
    return replace(state, cursor=Cursor(task_id))


def finish_task(state: RunState, task_id: str, succeeded: bool) -> RunState:
    """Задача завершена: `done` либо `failed`; курсор снимается."""
    status = TaskStatus.DONE if succeeded else TaskStatus.FAILED
    state = _update_task(state, task_id, status=status)
    return replace(state, cursor=None)


def pause(state: RunState, reason: str) -> RunState:
    """Постановка на паузу: прогон не завершён, причина — код."""
    return replace(state, finished=False, reason=reason)


def finish_run(state: RunState, reason: str) -> RunState:
    """Прогон дошёл до конца: признак завершения и код причины."""
    return replace(state, finished=True, reason=reason, cursor=None)


def resume_run(state: RunState, config: dict[str, Any]) -> RunState:
    """Возобновление: прогон снова идёт с применяемой сейчас конфигурацией."""
    return replace(state, finished=False, reason=None, config=dict(config))


def unfinished_ids(state: RunState) -> list[str]:
    """Идентификаторы незавершённых задач в порядке списка."""
    return [t.task_id for t in state.tasks if t.status in UNFINISHED]


def next_unfinished(state: RunState) -> TaskState | None:
    """Следующая невыполненная задача: первая `pending` или `in_progress`."""
    return next((t for t in state.tasks if t.status in UNFINISHED), None)


def done_count(state: RunState) -> int:
    return sum(1 for t in state.tasks if t.status is TaskStatus.DONE)


def reconcile(state: RunState, closed_ids: Iterable[str]) -> RunState:
    """Согласовать с `tasks.md`: отмеченная там задача выполнена, даже если обрыв
    случился между записью отметки и записью состояния."""
    closed = set(closed_ids)
    for task in state.tasks:
        if task.task_id in closed and task.status in UNFINISHED:
            state = finish_task(state, task.task_id, True)
    return state


# --- сериализация -----------------------------------------------------------


def to_dict(state: RunState) -> dict[str, Any]:
    return {
        "schema_version": state.schema_version,
        "run_id": state.run_id,
        "input_sha256": state.input_sha256,
        "config": state.config,
        "stages": [
            {"name": s.name, "status": s.status.value, "model": s.model}
            for s in state.stages
        ],
        "tasks": [
            {
                "task_id": t.task_id,
                "status": t.status.value,
                "attempts": t.attempts,
                "last_attempt": (
                    None
                    if t.last_attempt is None
                    else {
                        "number": t.last_attempt.number,
                        "success": t.last_attempt.success,
                        "error_code": t.last_attempt.error_code,
                    }
                ),
                "commit": t.commit,
            }
            for t in state.tasks
        ],
        "cursor": (
            None
            if state.cursor is None
            else {"task_id": state.cursor.task_id, "attempt": state.cursor.attempt}
        ),
        "finished": state.finished,
        "reason": state.reason,
    }


def encode_state(state: RunState) -> str:
    """Текст `state.json`: читаемый JSON с переводом строки в конце."""
    return json.dumps(to_dict(state), ensure_ascii=False, indent=2) + "\n"


def _need(data: Any, key: str, kind: type | tuple[type, ...], where: str) -> Any:
    if not isinstance(data, dict) or key not in data:
        raise StateFormatError(f"{where}: нет поля {key}")
    value = data[key]
    if isinstance(value, bool) and kind is not bool:
        raise StateFormatError(f"{where}.{key}: неверный тип")
    if not isinstance(value, kind):
        raise StateFormatError(f"{where}.{key}: неверный тип")
    return value


def _enum[E: StrEnum](cls: type[E], value: str, where: str) -> E:
    try:
        return cls(value)
    except ValueError as exc:
        raise StateFormatError(f"{where}: недопустимое значение {value!r}") from exc


def _opt_str(data: dict[str, Any], key: str, where: str) -> str | None:
    value = data.get(key)
    if value is not None and not isinstance(value, str):
        raise StateFormatError(f"{where}.{key}: неверный тип")
    return value


def _task_from(raw: Any) -> TaskState:
    where = "tasks[]"
    last_raw = raw.get("last_attempt") if isinstance(raw, dict) else None
    last = None
    if last_raw is not None:
        last = AttemptState(
            _need(last_raw, "number", int, "last_attempt"),
            _need(last_raw, "success", bool, "last_attempt"),
            _opt_str(last_raw, "error_code", "last_attempt"),
        )
    return TaskState(
        _need(raw, "task_id", str, where),
        _enum(TaskStatus, _need(raw, "status", str, where), "tasks[].status"),
        _need(raw, "attempts", int, where),
        last,
        _opt_str(raw, "commit", where),
    )


def decode_state(text: str) -> RunState:
    """Разобрать `state.json`; любое нарушение схемы — `StateFormatError`."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise StateFormatError(f"состояние не разбирается как JSON: {exc}") from exc
    version = _need(data, "schema_version", str, "state")
    if version.split(".")[0] != STATE_SCHEMA_VERSION.split(".")[0]:
        raise StateFormatError(f"неподдерживаемая схема состояния {version}")
    stages = tuple(
        StageState(
            _need(s, "name", str, "stages[]"),
            _enum(StageStatus, _need(s, "status", str, "stages[]"), "stages[].status"),
            _opt_str(s, "model", "stages[]"),
        )
        for s in _need(data, "stages", list, "state")
    )
    tasks = tuple(_task_from(t) for t in _need(data, "tasks", list, "state"))
    cursor_raw = data.get("cursor")
    cursor = None
    if cursor_raw is not None:
        attempt = cursor_raw.get("attempt") if isinstance(cursor_raw, dict) else None
        if attempt is not None and not isinstance(attempt, int):
            raise StateFormatError("cursor.attempt: неверный тип")
        cursor = Cursor(_need(cursor_raw, "task_id", str, "cursor"), attempt)
    state = RunState(
        _need(data, "run_id", str, "state"),
        _need(data, "input_sha256", str, "state"),
        _need(data, "config", dict, "state"),
        stages,
        tasks,
        cursor,
        _need(data, "finished", bool, "state"),
        _opt_str(data, "reason", "state"),
        version,
    )
    ids = [t.task_id for t in tasks]
    if len(set(ids)) != len(ids):
        raise StateFormatError("идентификаторы задач повторяются")
    if cursor is not None and cursor.task_id not in ids:
        raise StateFormatError("курсор указывает на неизвестную задачу")
    return state
