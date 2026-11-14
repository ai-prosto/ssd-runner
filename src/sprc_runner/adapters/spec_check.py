"""Проверки спецификации задачи (BEH-42), доступные CLI без внешних зависимостей."""

from pathlib import Path

from sprc_runner.ports import SpecCheckResult


class TestsCarrySpecChecks:
    """Проверки спецификации выражены тестами задачи и входят в зелёный запуск тестов.

    Отдельных исполняемых проверок у задачи нет, поэтому адаптер их не отклоняет:
    решение остаётся за кодом возврата тестов и ревью (единый критерий BEH-19).
    """

    __test__ = False

    def check(self, workdir: Path, task_id: str) -> SpecCheckResult:
        return SpecCheckResult(True, "проверки спецификации входят в запуск тестов")
