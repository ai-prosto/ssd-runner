"""BEH-10: цикл реализации идёт по задачам списка tasks.md в его порядке."""

import asyncio
from pathlib import Path

from sprc_runner.config import load_config
from sprc_runner.core.task_cycle import run_attempt, run_task_list, select_tasks
from sprc_runner.doubles import FakeAgent, FakeReviewer, FakeSpecChecker, FakeTestRunner

ROOT = Path(__file__).resolve().parents[2]

TASKS = """### TASK-003: Третья
P2 | ⬜ TODO   Est: 1d

**Checklist:**
- [ ] c

### TASK-001: Первая
P2 | ⬜ TODO   Est: 1d

**Checklist:**
- [ ] c

### TASK-002: Готовая
P2 | ✅ DONE   Est: 1d

**Checklist:**
- [x] c
"""


def test_loop_follows_list_order(tmp_path):
    assert [t.id for t in select_tasks(TASKS)] == ["TASK-003", "TASK-001"]
    agent, config = FakeAgent(), load_config(ROOT / "sprc-runner.toml")

    async def attempt(task, number):
        return await run_attempt(
            agent=agent,
            reviewer=FakeReviewer(),
            tests=FakeTestRunner(),
            spec_checker=FakeSpecChecker(),
            config=config,
            task=task,
            number=number,
            workdir=tmp_path,
        )

    records = asyncio.run(run_task_list(TASKS, attempt))
    assert [r.task_id for r in records] == ["TASK-003", "TASK-001"]
    assert [c.inputs[0].split(":")[0] for c in agent.calls] == ["TASK-003", "TASK-001"]
