"""Командная строка: разбор аргументов, команда `run`, коды возврата."""

import argparse
import hashlib
import sys
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import NoReturn, TextIO

import anyio

from sprc_runner.adapters.fs import LocalFiles
from sprc_runner.config import (
    EXIT_CONFIG_REFUSED,
    RunConfig,
    load_config_or_report,
)
from sprc_runner.core.checkpoint import RunCheckpoint
from sprc_runner.core.layout import resolve_target
from sprc_runner.core.prepare import (
    INPUT_NAME,
    STAGE_NAMES,
    TASKS_NAME,
    PrepareError,
    init_run,
    new_run_id,
    run_preparation,
)
from sprc_runner.core.progress import (
    ExitCode,
    Progress,
    exit_code_for,
    run_status,
)
from sprc_runner.core.state import (
    REASON_INTERRUPTED,
    STATE_NAME,
    StateFormatError,
    decode_state,
    done_count,
    new_state,
    next_unfinished,
    reconcile,
    resume_run,
    unfinished_ids,
)
from sprc_runner.core.task_cycle import run_attempt, run_tasks, select_tasks
from sprc_runner.core.tasks_md import TaskRecord, parse_tasks
from sprc_runner.ports import (
    AgentPort,
    GitPort,
    ReviewerPort,
    SpecCheckerPort,
    TestRunnerPort,
)

EXIT_OK = ExitCode.OK.value
EXIT_ABORTED = ExitCode.ABORTED.value
DEFAULT_CONFIG = "sprc-runner.toml"


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        print(f"{self.prog}: ошибка: {message}", file=sys.stderr)
        raise SystemExit(EXIT_CONFIG_REFUSED)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="sprc-runner")
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run", help="выполнить прогон по входному описанию")
    source = run.add_mutually_exclusive_group(required=True)
    source.add_argument("text", nargs="?", help="входное описание текстом")
    source.add_argument("--file", type=Path, help="путь к файлу с описанием")
    run.add_argument("--target", help="целевой каталог (по умолчанию .sdd-runs)")
    run.add_argument("--config", type=Path, default=Path(DEFAULT_CONFIG))
    resume = sub.add_parser("resume", help="продолжить прерванный прогон")
    resume.add_argument("run_dir", type=Path, help="каталог прогона")
    resume.add_argument("--config", type=Path, default=Path(DEFAULT_CONFIG))
    return parser


@dataclass
class LoopPorts:
    """Порты цикла задач; без них `run` выполняет только подготовку."""

    reviewer: ReviewerPort
    tests: TestRunnerPort
    spec_checker: SpecCheckerPort
    git: GitPort | None = None


def build_agent(run_dir: Path) -> AgentPort:
    """Единственная точка создания реального адаптера агента."""
    from sprc_runner.adapters.claude_sdk import ClaudeSdkAgent

    return ClaudeSdkAgent(run_dir / "transcripts")


def _read_description(args: argparse.Namespace, err: TextIO) -> bytes | None:
    if args.file is None:
        return str(args.text).encode("utf-8")
    try:
        return Path(args.file).read_bytes()
    except OSError as exc:
        print(f"Входной файл недоступен: {args.file}: {exc.strerror}", file=err)
        return None


def _fingerprint(description: bytes) -> str:
    return hashlib.sha256(description).hexdigest()


async def _loop_tasks(
    *,
    runner: AgentPort,
    loop: LoopPorts,
    config: RunConfig,
    checkpoint: RunCheckpoint,
    tasks_text: str,
    target: Path,
    charter_root: Path,
    progress: Progress,
    err: TextIO,
    only: Collection[str] | None = None,
    attempts_done: Mapping[str, int] | None = None,
) -> int:
    """Цикл задач с чекпойнтом; общий для `run` и `resume`."""

    async def attempt(task: TaskRecord, number: int):
        return await run_attempt(
            agent=runner,
            reviewer=loop.reviewer,
            tests=loop.tests,
            spec_checker=loop.spec_checker,
            config=config,
            task=task,
            number=number,
            workdir=target,
            git=loop.git,
            target=target,
            charter_root=charter_root,
        )

    result = await run_tasks(
        tasks_text,
        attempt,
        config.max_attempts,
        progress=progress,
        checkpoint=checkpoint,
        only=only,
        attempts_done=attempts_done,
    )
    state = checkpoint.state
    status = run_status(done_count(state), len(state.tasks), result.aborted)
    if not result.aborted:
        checkpoint.finished(status.value)
    progress.run_finished(status, done_count(state), len(state.tasks))
    if result.aborted:
        print("Прогон остановлен: отказ агента на реализации", file=err)
    return exit_code_for(status)


def _drive(
    body: Callable[[], Awaitable[int]],
    checkpoint: RunCheckpoint,
    run_dir: Path,
    err: TextIO,
) -> int:
    """Выполнить тело прогона; Ctrl-C ставит прогон на паузу и сохраняет состояние."""
    try:
        return anyio.run(body)
    except KeyboardInterrupt:
        if not checkpoint.state.finished:
            checkpoint.paused(REASON_INTERRUPTED)
        print(
            f"Прогон прерван пользователем. Возобновление: "
            f"sprc-runner resume {run_dir}",
            file=err,
        )
        return EXIT_ABORTED


def _run(
    args: argparse.Namespace,
    cwd: Path,
    agent: AgentPort | None,
    out: TextIO,
    err: TextIO,
    loop: LoopPorts | None = None,
) -> int:
    loaded = load_config_or_report((cwd / args.config), err)
    if not isinstance(loaded, RunConfig):
        return loaded
    config = loaded
    description = _read_description(args, err)
    if description is None:
        return EXIT_CONFIG_REFUSED
    target = resolve_target(args.target, cwd, config.target_dir)
    run_id = new_run_id()
    run_dir = init_run(target, run_id, description, config.run_dir)
    print(f"Каталог прогона: {run_dir}", file=out)

    checkpoint = RunCheckpoint(
        run_dir,
        new_state(run_id, _fingerprint(description), asdict(config), STAGE_NAMES),
        LocalFiles(),
        run_dir / TASKS_NAME,
    )
    checkpoint.save()
    progress = Progress(out)

    async def go() -> int:
        runner = agent or build_agent(run_dir)
        try:
            artifacts = await run_preparation(
                runner, config, run_dir, progress=progress, checkpoint=checkpoint
            )
            if loop is None:
                checkpoint.finished("prepared")
                return EXIT_OK
            tasks_text = artifacts.tasks.read_text(encoding="utf-8")
            checkpoint.tasks_registered([t.id for t in select_tasks(tasks_text)])
            return await _loop_tasks(
                runner=runner,
                loop=loop,
                config=config,
                checkpoint=checkpoint,
                tasks_text=tasks_text,
                target=target,
                charter_root=cwd,
                progress=progress,
                err=err,
            )
        except PrepareError as exc:
            checkpoint.paused(f"{exc.stage}_{exc.result.outcome.value}")
            print(f"Прогон остановлен: {exc}", file=err)
            return EXIT_ABORTED
        finally:
            await runner.close()

    return _drive(go, checkpoint, run_dir, err)


def _refuse(err: TextIO, message: str) -> int:
    print(f"Возобновление невозможно: {message}", file=err)
    return EXIT_CONFIG_REFUSED


def _resume(
    args: argparse.Namespace,
    cwd: Path,
    agent: AgentPort | None,
    out: TextIO,
    err: TextIO,
    loop: LoopPorts | None = None,
) -> int:
    loaded = load_config_or_report((cwd / args.config), err)
    if not isinstance(loaded, RunConfig):
        return loaded
    config = loaded
    run_dir = Path(args.run_dir)
    run_dir = (run_dir if run_dir.is_absolute() else cwd / run_dir).resolve()
    try:
        state = decode_state((run_dir / STATE_NAME).read_text(encoding="utf-8"))
        description = (run_dir / INPUT_NAME).read_bytes()
        tasks_text = (run_dir / TASKS_NAME).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, StateFormatError) as exc:
        return _refuse(err, f"каталог прогона не читается: {exc}")
    if _fingerprint(description) != state.input_sha256:
        return _refuse(err, "входное описание не совпадает с отпечатком в состоянии")
    if not state.tasks:
        return _refuse(err, "подготовка не завершена, списка задач в состоянии нет")
    if loop is None:
        return _refuse(err, "не заданы порты цикла задач")

    closed = [t.id for t in parse_tasks(tasks_text) if t.status == "done"]
    state = resume_run(reconcile(state, closed), asdict(config))
    target = run_dir.parent.parent
    checkpoint = RunCheckpoint(run_dir, state, LocalFiles(), run_dir / TASKS_NAME)
    checkpoint.save()
    print(f"Каталог прогона: {run_dir}", file=out)
    following = next_unfinished(state)
    if following is not None:
        print(f"Возобновление с задачи {following.task_id}", file=out)
    progress = Progress(out)

    async def go() -> int:
        runner = agent or build_agent(run_dir)
        try:
            return await _loop_tasks(
                runner=runner,
                loop=loop,
                config=config,
                checkpoint=checkpoint,
                tasks_text=tasks_text,
                target=target,
                charter_root=cwd,
                progress=progress,
                err=err,
                only=unfinished_ids(state),
                attempts_done={t.task_id: t.attempts for t in state.tasks},
            )
        finally:
            await runner.close()

    return _drive(go, checkpoint, run_dir, err)


def main(
    argv: Sequence[str] | None = None,
    *,
    cwd: Path | None = None,
    agent: AgentPort | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
    loop: LoopPorts | None = None,
) -> int:
    """Точка входа CLI; возвращает код возврата."""
    out = out or sys.stdout
    err = err or sys.stderr
    args = build_parser().parse_args(argv)
    if args.command == "run":
        return _run(args, cwd or Path.cwd(), agent, out, err, loop)
    if args.command == "resume":
        return _resume(args, cwd or Path.cwd(), agent, out, err, loop)
    print("sprc-runner", file=out)
    return EXIT_OK
