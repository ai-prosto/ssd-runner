"""BEH-27: файл задач совпадает по формату с файлом spec-runner."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from sprc_runner.core import tasks_md as tm

SAMPLE = """### TASK-001: Первая
P1 | ⬜ TODO   Est: 1d

Описание.
**Depends on:** [TASK-000]

**Checklist:**
- [ ] сделать
- [x] готово

**Traces to:** [FR-01], [FR-02]

### TASK-002: Вторая
P2 | 🔄 IN_PROGRESS   Est: 0.5d

**Checklist:**
- [ ] ещё
"""

SIBLING = Path(__file__).resolve().parents[3] / "spec-runner"

_DRIVER = """
import json, sys
from pathlib import Path
from spec_runner import task
f = Path(sys.argv[1])
if len(sys.argv) > 3:
    task.update_task_status(f, sys.argv[2], sys.argv[3])
print(json.dumps([
    [t.id, t.name, t.priority, t.status, t.estimate, t.checklist, t.depends_on]
    for t in task.parse_tasks(f)
]))
"""


def _theirs(path: Path, *update: str) -> list:
    """Разобрать файл инструментарием spec-runner в его собственном окружении."""
    if not (SIBLING / "src" / "spec_runner").is_dir():
        pytest.skip("соседний репозиторий spec-runner недоступен")
    proc = subprocess.run(
        ["uv", "run", "--project", str(SIBLING), "python", "-c", _DRIVER,
         str(path), *update],
        capture_output=True, text=True, env={**os.environ, "VIRTUAL_ENV": ""},
    )  # fmt: skip
    if proc.returncode != 0:
        pytest.skip(f"spec-runner не запускается: {proc.stderr[-200:]}")
    return json.loads(proc.stdout.splitlines()[-1])


def _ours(text: str) -> list:
    return [
        [t.id, t.name, t.priority, t.status, t.estimate,
         [list(c) for c in t.checklist], t.depends_on]
        for t in tm.parse_tasks(text)
    ]  # fmt: skip


def test_parse_matches_spec_runner(tmp_path):
    f = tmp_path / "tasks.md"
    f.write_text(SAMPLE, encoding="utf-8")
    assert _ours(SAMPLE) == _theirs(f)


def test_written_file_parsed_by_spec_runner(tmp_path):
    text = tm.set_status(SAMPLE, "TASK-001", "done")
    text = tm.set_checklist(text, "TASK-001", 0, True)
    rendered = tm.render_tasks(tm.parse_tasks(text))
    for i, variant in enumerate((text, rendered)):
        f = tmp_path / f"tasks{i}.md"
        f.write_text(variant, encoding="utf-8")
        assert _ours(variant) == _theirs(f)
    assert _ours(text)[0][3] == "done"


def test_spec_runner_status_writer_readable_by_us(tmp_path):
    f = tmp_path / "tasks.md"
    f.write_text(SAMPLE, encoding="utf-8")
    theirs = _theirs(f, "TASK-002", "done")
    assert theirs[1][3] == "done"
    assert _ours(f.read_text(encoding="utf-8")) == theirs


def test_set_status_touches_only_meta_line():
    out = tm.set_status(SAMPLE, "TASK-001", "done")
    diff = [
        (a, b)
        for a, b in zip(SAMPLE.split("\n"), out.split("\n"), strict=True)
        if a != b
    ]
    assert diff == [("P1 | ⬜ TODO   Est: 1d", "P1 | ✅ DONE   Est: 1d")]


def test_set_status_plain_meta_and_bounded():
    text = "### A-1: x\nP1 | TODO\n\n### A-2: y\nP1 | TODO\n"
    out = tm.set_status(text, "A-1", "blocked")
    assert out == "### A-1: x\nP1 | ⏸️ BLOCKED\n\n### A-2: y\nP1 | TODO\n"


def test_no_own_fields_in_render():
    out = tm.render_tasks(tm.parse_tasks(SAMPLE))
    assert {ln.split(":**")[0] for ln in out.split("\n") if ln.startswith("**")} == {
        "**Depends on",
        "**Checklist",
        "**Traces to",
    }


def test_frontmatter_ignored():
    assert len(tm.parse_tasks("---\na: 1\n---\n" + SAMPLE)) == 2


def test_errors():
    with pytest.raises(tm.TasksFormatError):
        tm.set_status(SAMPLE, "NOPE-1", "done")
    with pytest.raises(tm.TasksFormatError):
        tm.set_status(SAMPLE, "TASK-001", "weird")
    with pytest.raises(tm.TasksFormatError):
        tm.set_checklist(SAMPLE, "TASK-001", 9, True)
    with pytest.raises(tm.TasksFormatError):
        tm.set_status("### A-1: x\nнет меты\n", "A-1", "done")


def test_validate_ok_and_failures():
    assert len(tm.validate_tasks(SAMPLE)) == 2
    with pytest.raises(tm.TasksFormatError, match="ни одной"):
        tm.validate_tasks("просто текст")
    with pytest.raises(tm.TasksFormatError, match="повторя"):
        tm.validate_tasks(SAMPLE + "\n### TASK-001: dup\nP1 | TODO\n")
    with pytest.raises(tm.TasksFormatError, match="меты"):
        tm.validate_tasks("### A-1: x\nтекст\n")
    with pytest.raises(tm.TasksFormatError, match="пригодной"):
        tm.validate_tasks("### A-1: x\nP1 | DONE\n\n**Checklist:**\n- [x] a\n")
