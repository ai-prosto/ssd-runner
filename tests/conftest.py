import asyncio
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from sprc_runner.adapters.fs import LocalFiles
from sprc_runner.config import RunConfig, load_config
from sprc_runner.core.logbook import EventType, Journal, read_log
from sprc_runner.core.task_cycle import run_attempt, run_tasks
from sprc_runner.doubles import (
    FakeAgent,
    FakeClock,
    FakeReviewer,
    FakeSpecChecker,
    FakeTestRunner,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def config_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("cfg") / "sprc-runner.toml"
    shutil.copy(ROOT / "sprc-runner.toml", path)
    return path


@pytest.fixture
def set_models(config_file: Path) -> Callable[..., None]:
    """Переписать ключи моделей в копии конфигурации."""

    def apply(**models: str) -> None:
        lines = []
        for line in config_file.read_text(encoding="utf-8").splitlines():
            key = line.split("=")[0].strip()
            if key in models:
                line = f'{key} = "{models[key]}"'
            lines.append(line)
        config_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return apply


TASKS_MD = """### TASK-001: Первая
P2 | ⬜ TODO   Est: 1d

**Checklist:**
- [ ] a

### TASK-002: Вторая
P2 | ⬜ TODO   Est: 1d

**Checklist:**
- [ ] b
"""


def _run_logged(tmp_path: Path, returncodes: list[int], max_attempts: int = 2):
    """Прогнать TASKS_MD с журналом; вернуть конфигурацию, путь лога, события, текст."""
    config: RunConfig = load_config(ROOT / "sprc-runner.toml")
    path = tmp_path / "log.jsonl"
    journal = Journal(path, "run-1", LocalFiles(), FakeClock(10.0))
    tests = FakeTestRunner(returncodes)
    agent, reviewer, checker = FakeAgent(), FakeReviewer(), FakeSpecChecker()

    async def attempt(task, number):
        return await run_attempt(
            agent=agent,
            reviewer=reviewer,
            tests=tests,
            spec_checker=checker,
            config=config,
            task=task,
            number=number,
            workdir=tmp_path,
            journal=journal,
        )

    journal.start(EventType.RUN_STARTED)
    result = asyncio.run(run_tasks(TASKS_MD, attempt, max_attempts, journal))
    journal.finish(EventType.RUN_FINISHED)
    return config, path, read_log(path, LocalFiles()), result


@pytest.fixture
def run_logged() -> Callable[..., tuple]:
    """Прогон двух задач с журналом: (конфигурация, путь, события, результат)."""
    return _run_logged
