"""BEH-28: отметка о выполнении — в формате spec-runner и только после успеха."""

import asyncio
from pathlib import Path

from sprc_runner.config import load_config
from sprc_runner.core.task_cycle import run_attempt, run_tasks
from sprc_runner.core.tasks_md import mark_completed, parse_tasks
from sprc_runner.doubles import FakeAgent, FakeReviewer, FakeSpecChecker, FakeTestRunner

ROOT = Path(__file__).resolve().parents[2]

TEXT = """# Tasks

### TASK-001: Первая
P2 | ⬜ TODO   Est: 1d

**Checklist:**
- [ ] a
- [ ] b

### TASK-002: Вторая
P2 | ⬜ TODO   Est: 1d

**Checklist:**
- [ ] c
"""


def run(tmp_path, returncodes, max_attempts, seen=None):
    config = load_config(ROOT / "sprc-runner.toml")
    tests = FakeTestRunner(returncodes)

    async def attempt(task, number):
        if seen is not None:
            seen.append(number)
        return await run_attempt(
            agent=FakeAgent(),
            reviewer=FakeReviewer(),
            tests=tests,
            spec_checker=FakeSpecChecker(),
            config=config,
            task=task,
            number=number,
            workdir=tmp_path,
        )

    return asyncio.run(run_tasks(TEXT, attempt, max_attempts))


def test_mark_format_matches_spec_runner():
    out = mark_completed(TEXT, "TASK-001")
    assert "P2 | ✅ DONE   Est: 1d" in out
    first, second = parse_tasks(out)
    assert first.status == "done" and all(c for _, c in first.checklist)
    assert second.status == "todo" and not any(c for _, c in second.checklist)
    assert out.replace("✅ DONE", "⬜ TODO").replace("[x]", "[ ]") == TEXT


def test_mark_only_on_success_not_on_exhaustion(tmp_path):
    result = run(tmp_path, [1, 1, 0], 2)
    first, second = parse_tasks(result.tasks_text)
    assert first.status == "todo" and not any(c for _, c in first.checklist)
    assert second.status == "done"


def test_no_mark_before_final_success(tmp_path):
    result = run(tmp_path, [1, 0, 0], 3)
    assert [len(r.attempts) for r in result.results] == [2, 1]
    assert all(t.status == "done" for t in parse_tasks(result.tasks_text))
