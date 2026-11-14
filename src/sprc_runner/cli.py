"""Командная строка: разбор аргументов, команда `run`, коды возврата."""

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn, TextIO

import anyio

from sprc_runner.config import (
    EXIT_CONFIG_REFUSED,
    RunConfig,
    load_config_or_report,
)
from sprc_runner.core.layout import resolve_target
from sprc_runner.core.prepare import (
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
from sprc_runner.core.task_cycle import run_attempt, run_tasks
from sprc_runner.core.tasks_md import TaskRecord
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

    progress = Progress(out)

    async def go() -> int:
        runner = agent or build_agent(run_dir)
        try:
            artifacts = await run_preparation(
                runner, config, run_dir, progress=progress
            )
            if loop is None:
                return EXIT_OK
            tasks_text = artifacts.tasks.read_text(encoding="utf-8")

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
                    charter_root=cwd,
                )

            result = await run_tasks(
                tasks_text, attempt, config.max_attempts, progress=progress
            )
            artifacts.tasks.write_text(result.tasks_text, encoding="utf-8")
            status = run_status(result.succeeded_count, result.total, result.aborted)
            progress.run_finished(status, result.succeeded_count, result.total)
            if result.aborted:
                print("Прогон остановлен: отказ агента на реализации", file=err)
            return exit_code_for(status)
        except PrepareError as exc:
            print(f"Прогон остановлен: {exc}", file=err)
            return EXIT_ABORTED
        finally:
            await runner.close()

    return anyio.run(go)


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
    print("sprc-runner", file=out)
    return EXIT_OK
