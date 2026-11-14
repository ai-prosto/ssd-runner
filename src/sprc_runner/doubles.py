"""Подставные реализации портов: заранее заданные исходы, без сети и ключей."""

from collections import deque
from collections.abc import Iterable
from pathlib import Path

from sprc_runner.ports import (
    Outcome,
    ReviewVerdict,
    StageRequest,
    StageResult,
    TestRunResult,
)


def limit_result() -> StageResult:
    """Результат с отказом по лимитам подписки."""
    return StageResult(Outcome.SUBSCRIPTION_LIMIT, "", None, "limit")


class FakeAgent:
    """Агент: отдаёт исходы по очереди, считает инвокации."""

    def __init__(self, results: Iterable[StageResult] = ()) -> None:
        self._queue = deque(results)
        self.calls: list[StageRequest] = []
        self.closed = False

    @property
    def invocations(self) -> int:
        return len(self.calls)

    def fail_with_limit(self) -> None:
        """Следующая инвокация завершится отказом по лимитам."""
        self._queue.appendleft(limit_result())

    async def invoke(self, request: StageRequest) -> StageResult:
        self.calls.append(request)
        if self._queue:
            return self._queue.popleft()
        return StageResult(Outcome.DONE, "ok", "fake://transcript", "end_turn")

    async def close(self) -> None:
        self.closed = True


class FakeReviewer:
    """Ревьюер с заданными вердиктами."""

    def __init__(self, verdicts: Iterable[ReviewVerdict] = ()) -> None:
        self._queue = deque(verdicts)
        self.calls: list[StageRequest] = []

    @property
    def invocations(self) -> int:
        return len(self.calls)

    async def review(self, request: StageRequest) -> ReviewVerdict:
        self.calls.append(request)
        if self._queue:
            return self._queue.popleft()
        return ReviewVerdict(True, "", "fake-reviewer")


class FakeGit:
    """Git в памяти: дифф задан заранее, действия записываются."""

    def __init__(self, diff: str = "") -> None:
        self._diff = diff
        self.staged: list[list[str]] = []
        self.commits: list[str] = []
        self.rollbacks = 0

    @property
    def invocations(self) -> int:
        return len(self.staged) + len(self.commits) + self.rollbacks

    def diff(self, workdir: Path) -> str:
        return self._diff

    def stage_paths(self, workdir: Path, paths: list[str]) -> None:
        self.staged.append(list(paths))

    def commit(self, workdir: Path, message: str) -> str:
        self.commits.append(message)
        return f"fake{len(self.commits):04d}"

    def rollback(self, workdir: Path) -> None:
        self.rollbacks += 1


class FakeTestRunner:
    """Раннер тестов с заданными кодами возврата."""

    __test__ = False

    def __init__(self, returncodes: Iterable[int] = ()) -> None:
        self._queue = deque(returncodes)
        self.calls: list[str] = []

    @property
    def invocations(self) -> int:
        return len(self.calls)

    def run(self, workdir: Path, command: str) -> TestRunResult:
        self.calls.append(command)
        code = self._queue.popleft() if self._queue else 0
        return TestRunResult(command, code, f"fake://tests/{len(self.calls)}")


class FakeClock:
    """Часы, идущие только вручную."""

    def __init__(self, start: float = 0.0) -> None:
        self._t = start

    def advance(self, seconds: float) -> None:
        self._t += seconds

    def now(self) -> float:
        return self._t

    def monotonic(self) -> float:
        return self._t


class EchoAgent(FakeAgent):
    """Агент, ответ которого выводится из входов этапа (для проверки цепочки)."""

    async def invoke(self, request: StageRequest) -> StageResult:
        self.calls.append(request)
        text = f"[{request.stage}|{request.model}] " + " || ".join(request.inputs[1:])
        return StageResult(Outcome.DONE, text, None, "end_turn")
