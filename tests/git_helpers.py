"""Помощники тестов: настоящий git-репозиторий во временном каталоге и попытка с git."""

import asyncio
import subprocess
from collections.abc import Callable
from pathlib import Path

from sprc_runner.adapters.fs import LocalFiles
from sprc_runner.adapters.git import GitRepo
from sprc_runner.config import load_config
from sprc_runner.core.logbook import Journal, read_log
from sprc_runner.core.task_cycle import AttemptRecord, run_attempt, select_tasks
from sprc_runner.doubles import (
    FakeAgent,
    FakeClock,
    FakeReviewer,
    FakeSpecChecker,
    FakeTestRunner,
)
from sprc_runner.ports import Outcome, StageRequest, StageResult

ROOT = Path(__file__).resolve().parents[1]
TASK_MD = "### TASK-001: Первая\nP2 | ⬜ TODO   Est: 1d\n\n**Checklist:**\n- [ ] a\n"
CON07_FILES = (
    "profiles/roles.yaml",
    "scripts/review/local.sh",
    "spec-runner.config.yaml",
    ".github/codex/review-prompt.md",
)


def git(cwd: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )
    return out.stdout


def make_repo(root: Path) -> Path:
    """Репозиторий с файлами DarkFactory и одним готовым файлом кода."""
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q")
    git(root, "config", "user.name", "t")
    git(root, "config", "user.email", "t@t")
    for rel in CON07_FILES:
        write(root / rel, "original\n")
    write(root / "README.md", "readme\n")
    write(root / ".gitignore", ".sprc/\n")
    write(root / "app" / "core.py", "".join(f"line {i}\n" for i in range(100)))
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    return root


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class MutatingAgent(FakeAgent):
    """Агент, который при инвокации меняет файлы так, как задал тест."""

    def __init__(self, mutate: Callable[[], object]) -> None:
        super().__init__()
        self._mutate = mutate

    async def invoke(self, request: StageRequest) -> StageResult:
        self._mutate()
        return StageResult(Outcome.DONE, "ok", None, "end_turn")


def attempt(
    repo: Path,
    target: Path,
    mutate: Callable[[], object],
    number: int = 1,
) -> tuple[AttemptRecord, list[dict], GitRepo]:
    """Одна попытка с настоящим git; возвращает запись, события журнала и адаптер."""
    adapter = GitRepo(target, repo)
    target.mkdir(parents=True, exist_ok=True)
    log = target / ".sprc" / "run-1" / "log.jsonl"
    journal = Journal(log, "run-1", LocalFiles(), FakeClock(1.0))
    task = select_tasks(TASK_MD)[0]
    record = asyncio.run(
        run_attempt(
            agent=MutatingAgent(mutate),
            reviewer=FakeReviewer(),
            tests=FakeTestRunner(),
            spec_checker=FakeSpecChecker(),
            config=load_config(ROOT / "sprc-runner.toml"),
            task=task,
            number=number,
            workdir=target,
            journal=journal,
            git=adapter,
            target=target,
            charter_root=repo,
            files=LocalFiles(),
            evidence_dir=target / ".sprc" / "run-1" / "evidence",
        )
    )
    return record, read_log(log, LocalFiles()), adapter
