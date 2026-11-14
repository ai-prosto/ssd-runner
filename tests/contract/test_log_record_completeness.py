"""BEH-24 / DEL-32: полный состав FR-06 по каждой задаче; внешний парсер."""

import json

from sprc_runner.core.logbook import (
    TASK_ROW_FIELDS,
    completeness,
    parse_log,
    project_tasks,
)
from sprc_runner.core.tasks_md import parse_tasks


def test_every_task_has_all_fields(tmp_path, run_logged):
    config, path, events, _ = run_logged(tmp_path, [1, 0, 1, 1])
    rows = project_tasks(events)
    assert [r.task_id for r in rows] == ["TASK-001", "TASK-002"]
    assert all(r.complete for r in rows)
    first, second = rows
    assert first.fields["success"] is True
    assert first.fields["attempt"] == 2 and first.fields["attempts_total"] == 2
    assert first.fields["error_code"] is None
    assert second.fields["success"] is False
    assert second.fields["error_code"] == "tests_failed"
    assert second.fields["model"] == config.model_implement
    assert set(TASK_ROW_FIELDS) <= set(first.fields)


def test_external_parser_computes_share_from_two_artifacts(tmp_path, run_logged):
    _, path, _, result = run_logged(tmp_path, [0, 0])
    ids = [t.id for t in parse_tasks(result.tasks_text)]
    report = completeness(path.read_text(encoding="utf-8"), ids)
    assert (report.total, report.complete, report.ratio) == (2, 2, 1.0)
    assert report.defects == {}


def test_missing_field_or_task_is_defect(tmp_path, run_logged):
    _, path, _, _ = run_logged(tmp_path, [0, 0])
    lines = path.read_text(encoding="utf-8").splitlines()
    out = []
    for line in lines:
        rec = json.loads(line)
        if rec["event"] == "task_finished" and rec["task_id"] == "TASK-002":
            del rec["tests_returncode"]
        if rec["event"] == "task_finished" and rec["task_id"] == "TASK-001":
            continue  # у первой задачи нет записи вовсе
        out.append(json.dumps(rec))
    report = completeness("\n".join(out) + "\n", ["TASK-001", "TASK-002"])
    assert report.complete == 0 and report.ratio == 0.0
    assert report.defects["TASK-002"] == ["tests_returncode"]
    assert report.defects["TASK-001"] == list(TASK_ROW_FIELDS)


def test_failed_task_without_error_code_is_defect():
    event = {
        "schema_version": "1.0",
        "run_id": "r",
        "seq": 1,
        "ts": 1.0,
        "event": "task_finished",
        "task_id": "T-1",
        "started_at": 1.0,
        "model": "m",
        "tests_returncode": 1,
        "review_approved": True,
        "attempt": 1,
        "attempts_total": 1,
        "success": False,
        "decision": "done",
    }
    (row,) = project_tasks(parse_log(json.dumps(event) + "\n"))
    assert row.missing == ["error_code"]
