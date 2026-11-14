"""Прогресс прогона в терминале (BEH-15) и коды возврата исходов (BEH-16).

Строки прогресса пишутся по ходу прогона и сбрасываются в поток сразу, чтобы
оператор видел этап, задачу, номер попытки и исход предыдущей задачи до
завершения прогона, а не единым блоком в конце. Код возврата выбирает одна
функция `exit_code_for` по итогу прогона.
"""

from enum import IntEnum, StrEnum
from typing import TextIO

from sprc_runner.config import EXIT_CONFIG_REFUSED


class RunStatus(StrEnum):
    """Три исхода прогона; отказ по конфигурации исходом прогона не является."""

    ALL_SUCCEEDED = "all_succeeded"
    PARTIAL = "partial"
    ABORTED = "aborted"


class ExitCode(IntEnum):
    """Четыре различимых кода возврата."""

    OK = 0
    PARTIAL = 1
    ABORTED = 3
    CONFIG_REFUSED = EXIT_CONFIG_REFUSED


def run_status(succeeded: int, total: int, aborted: bool) -> RunStatus:
    """Исход прогона: аварийная остановка сильнее частичного успеха."""
    if aborted:
        return RunStatus.ABORTED
    if succeeded == total:
        return RunStatus.ALL_SUCCEEDED
    return RunStatus.PARTIAL


def exit_code_for(status: RunStatus) -> int:
    return {
        RunStatus.ALL_SUCCEEDED: ExitCode.OK,
        RunStatus.PARTIAL: ExitCode.PARTIAL,
        RunStatus.ABORTED: ExitCode.ABORTED,
    }[status].value


class Progress:
    """Построчный вывод прогресса; каждая строка сразу сбрасывается в поток."""

    def __init__(self, out: TextIO) -> None:
        self._out = out

    def _line(self, text: str) -> None:
        print(text, file=self._out, flush=True)

    def stage(self, stage: str) -> None:
        self._line(f"[этап] {stage}")

    def task_started(self, task_id: str, name: str = "") -> None:
        self._line(f"[задача] {task_id}" + (f": {name}" if name else ""))

    def attempt_started(self, task_id: str, number: int, limit: int) -> None:
        self._line(f"[попытка] {task_id}: {number}/{limit}")

    def attempt_finished(
        self, task_id: str, number: int, success: bool, error_code: str | None
    ) -> None:
        verdict = "успех" if success else f"неуспех ({error_code or 'unknown'})"
        self._line(f"[исход] {task_id} попытка {number}: {verdict}")

    def task_finished(self, task_id: str, succeeded: bool, attempts: int) -> None:
        verdict = "выполнена" if succeeded else "провалена"
        self._line(f"[итог] {task_id}: {verdict}, попыток: {attempts}")

    def run_finished(self, status: RunStatus, succeeded: int, total: int) -> None:
        self._line(f"[прогон] {status.value}: выполнено {succeeded} из {total}")
