"""Чекпойнт прогона: каждая засечка перезаписывает `state.json` целиком атомарно.

Засечки (DEL-41): старт прогона, завершение этапа, взятие задачи в работу, старт
попытки, завершение попытки, завершение задачи, постановка на паузу. Переходы
считает `core/state.py`; здесь они только применяются и сбрасываются на диск.
При завершении задачи сначала пишется `tasks.md` с отметкой, затем состояние:
обрыв между ними чинится сверкой при возобновлении (`state.reconcile`).
"""

from pathlib import Path

from sprc_runner.core import state as st
from sprc_runner.ports import FilePort


class RunCheckpoint:
    """Хранитель состояния прогона в каталоге прогона."""

    def __init__(
        self,
        run_dir: Path,
        state: st.RunState,
        files: FilePort,
        tasks_path: Path | None = None,
    ) -> None:
        self._path = run_dir / st.STATE_NAME
        self._state = state
        self._files = files
        self._tasks_path = tasks_path

    @property
    def state(self) -> st.RunState:
        return self._state

    def _apply(self, state: st.RunState) -> None:
        self._state = state
        self._files.write_atomic(self._path, st.encode_state(state))

    def save(self) -> None:
        """Записать текущее состояние (старт прогона и возобновления)."""
        self._apply(self._state)

    def stage_finished(self, stage: str, model: str, ok: bool) -> None:
        self._apply(st.finish_stage(self._state, stage, model, ok))

    def tasks_registered(self, task_ids: list[str]) -> None:
        self._apply(st.register_tasks(self._state, task_ids))

    def task_taken(self, task_id: str) -> None:
        self._apply(st.take_task(self._state, task_id))

    def attempt_started(self, task_id: str, number: int) -> None:
        self._apply(st.start_attempt(self._state, task_id, number))

    def attempt_finished(
        self,
        task_id: str,
        number: int,
        success: bool,
        error_code: str | None,
        commit: str | None,
        counted: bool,
    ) -> None:
        self._apply(
            st.finish_attempt(
                self._state, task_id, number, success, error_code, commit, counted
            )
        )

    def task_finished(
        self, task_id: str, succeeded: bool, tasks_text: str | None = None
    ) -> None:
        if succeeded and tasks_text is not None and self._tasks_path is not None:
            self._files.write_atomic(self._tasks_path, tasks_text)
        self._apply(st.finish_task(self._state, task_id, succeeded))

    def paused(self, reason: str) -> None:
        self._apply(st.pause(self._state, reason))

    def finished(self, reason: str) -> None:
        self._apply(st.finish_run(self._state, reason))
