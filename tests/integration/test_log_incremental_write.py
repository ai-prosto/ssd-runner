"""BEH-06: журнал пишется по ходу прогона и читаем после аварийной остановки."""

import pytest

from sprc_runner.adapters.fs import LocalFiles
from sprc_runner.core.logbook import EventType, Journal, read_log
from sprc_runner.doubles import FakeClock


def test_completed_records_survive_crash_mid_run(tmp_path):
    path = tmp_path / "log.jsonl"
    journal = Journal(path, "run-1", LocalFiles(), FakeClock(1.0))
    journal.start(EventType.RUN_STARTED)
    journal.start(EventType.TASK_STARTED, task_id="TASK-001")
    journal.start(EventType.ATTEMPT_STARTED, task_id="TASK-001", attempt=1)
    journal.finish(EventType.ATTEMPT_FINISHED, task_id="TASK-001", attempt=1)
    journal.finish(EventType.TASK_FINISHED, task_id="TASK-001")
    with pytest.raises(KeyboardInterrupt):
        journal.start(EventType.TASK_STARTED, task_id="TASK-002")
        raise KeyboardInterrupt  # аварийная остановка без закрытия журнала

    events = read_log(path, LocalFiles())
    assert [e["event"] for e in events] == [
        "run_started",
        "task_started",
        "attempt_started",
        "attempt_finished",
        "task_finished",
        "task_started",
    ]
    assert [e["seq"] for e in events] == [1, 2, 3, 4, 5, 6]


def test_torn_last_line_is_ignored_and_earlier_kept(tmp_path):
    path = tmp_path / "log.jsonl"
    journal = Journal(path, "run-1", LocalFiles(), FakeClock())
    journal.start(EventType.RUN_STARTED)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('{"schema_version":"1.0","run_id":"run-1","se')
    events = read_log(path, LocalFiles())
    assert [e["event"] for e in events] == ["run_started"]
