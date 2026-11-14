"""BEH-15: этап, задача, попытка и исход видны по ходу прогона, не блоком в конце."""

import io
from pathlib import Path

from sprc_runner.cli import LoopPorts, main
from sprc_runner.core.progress import Progress, RunStatus
from sprc_runner.doubles import (
    EchoAgent,
    FakeReviewer,
    FakeSpecChecker,
    FakeTestRunner,
)
from sprc_runner.ports import StageRequest, StageResult

TASKS = (
    "### TASK-001: Первая\nP2 | ⬜ TODO   Est: 1d\n\n**Checklist:**\n- [ ] a\n\n"
    "### TASK-002: Вторая\nP2 | ⬜ TODO   Est: 1d\n\n**Checklist:**\n- [ ] b\n"
)


class SnapshotAgent(EchoAgent):
    """Агент, фиксирующий, что уже выведено в терминал к моменту инвокации."""

    def __init__(self, out: io.StringIO) -> None:
        super().__init__()
        self.out = out
        self.seen: list[tuple[str, str]] = []

    async def invoke(self, request: StageRequest) -> StageResult:
        self.seen.append((request.stage, self.out.getvalue()))
        result = await super().invoke(request)
        if request.stage == "prepare_tasks":
            return StageResult(result.outcome, TASKS, None, "end_turn")
        return result


def test_lines_appear_during_run(tmp_path: Path, config_file: Path) -> None:
    out = io.StringIO()
    agent = SnapshotAgent(out)
    loop = LoopPorts(FakeReviewer(), FakeTestRunner([1, 0, 0]), FakeSpecChecker())
    code = main(
        ["run", "Описание.", "--config", str(config_file)],
        cwd=tmp_path,
        agent=agent,
        out=out,
        loop=loop,
    )
    assert code == 0
    seen = dict(agent.seen)
    # строка этапа уже выведена к моменту его инвокации, следующих ещё нет
    assert seen["prepare_requirements"].endswith("[этап] prepare_requirements\n")
    assert "[этап] prepare_tasks" not in seen["prepare_requirements"]
    first_impl = next(snap for st, snap in agent.seen if st == "implement")
    assert first_impl.endswith("[попытка] TASK-001: 1/3\n")
    text = out.getvalue()
    order = [
        "[этап] prepare_tasks",
        "[задача] TASK-001: Первая",
        "[попытка] TASK-001: 1/3",
        "[исход] TASK-001 попытка 1: неуспех (tests_failed)",
        "[попытка] TASK-001: 2/3",
        "[исход] TASK-001 попытка 2: успех",
        "[итог] TASK-001: выполнена, попыток: 2",
        "[задача] TASK-002: Вторая",
    ]
    positions = [text.index(s) for s in order]
    assert positions == sorted(positions)
    assert "[прогон] all_succeeded: выполнено 2 из 2" in text


def test_second_task_start_shows_previous_outcome(
    tmp_path: Path, config_file: Path
) -> None:
    out = io.StringIO()
    agent = SnapshotAgent(out)
    loop = LoopPorts(FakeReviewer(), FakeTestRunner(), FakeSpecChecker())
    main(
        ["run", "Описание.", "--config", str(config_file)],
        cwd=tmp_path,
        agent=agent,
        out=out,
        loop=loop,
    )
    implement_snaps = [snap for st, snap in agent.seen if st == "implement"]
    assert "[итог] TASK-001: выполнена" in implement_snaps[1]
    assert "[итог] TASK-002" not in implement_snaps[1]


def test_each_line_is_flushed() -> None:
    class Spy(io.StringIO):
        flushes = 0

        def flush(self) -> None:
            Spy.flushes += 1
            super().flush()

    out = Spy()
    progress = Progress(out)
    progress.stage("s")
    progress.task_started("TASK-001")
    progress.attempt_finished("TASK-001", 1, False, None)
    progress.run_finished(RunStatus.PARTIAL, 0, 1)
    assert Spy.flushes == 4
    assert "[задача] TASK-001\n" in out.getvalue()
    assert "неуспех (unknown)" in out.getvalue()
