"""BEH-05: времена прогона и задач читаются из явных полей log.jsonl."""

from sprc_runner.adapters.fs import LocalFiles
from sprc_runner.core.logbook import EventType, Journal, read_log, timings
from sprc_runner.doubles import FakeClock


def test_run_and_task_timings_are_explicit_fields(tmp_path):
    clock = FakeClock(1000.0)
    path = tmp_path / "log.jsonl"
    journal = Journal(path, "run-1", LocalFiles(), clock)
    journal.start(EventType.RUN_STARTED)
    for task_id, secs in (("TASK-001", 5.0), ("TASK-002", 7.5)):
        journal.start(EventType.TASK_STARTED, task_id=task_id)
        clock.advance(secs)
        journal.finish(EventType.TASK_FINISHED, task_id=task_id, success=True)
    clock.advance(1.0)
    journal.finish(EventType.RUN_FINISHED)

    result = timings(read_log(path, LocalFiles()))
    assert result.run is not None
    assert (result.run.started_at, result.run.finished_at) == (1000.0, 1013.5)
    assert result.run.duration_s == 13.5
    first, second = result.tasks["TASK-001"], result.tasks["TASK-002"]
    assert (first.started_at, first.finished_at, first.duration_s) == (
        1000.0,
        1005.0,
        5.0,
    )
    assert (second.started_at, second.finished_at, second.duration_s) == (
        1005.0,
        1012.5,
        7.5,
    )
