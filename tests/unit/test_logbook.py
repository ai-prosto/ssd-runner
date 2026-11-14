"""DEL-24, DEL-28: конструирование записей и адаптеры файлов и часов."""

import json

from sprc_runner.adapters.clock import SystemClock
from sprc_runner.adapters.fs import LocalFiles
from sprc_runner.core.logbook import (
    EventType,
    Journal,
    build_event,
    encode_event,
    parse_log,
)
from sprc_runner.doubles import FakeClock


def test_build_event_envelope_omits_undefined_fields():
    e = build_event("task_started", run_id="r", seq=2, ts=3.0, task_id="T")
    assert e == {
        "schema_version": "1.0",
        "run_id": "r",
        "seq": 2,
        "ts": 3.0,
        "event": "task_started",
        "task_id": "T",
    }
    full = build_event(
        EventType.STEP_FINISHED,
        run_id="r",
        seq=1,
        ts=0,
        stage="s",
        task_id="T",
        attempt=1,
        model="m",
        returncode=0,
    )
    assert full["model"] == "m" and full["returncode"] == 0
    assert json.loads(encode_event(full)) == full


def test_unknown_event_type_rejected():
    import pytest

    with pytest.raises(ValueError):
        build_event("bogus", run_id="r", seq=1, ts=0)


def test_finish_without_start_and_stage_keys(tmp_path):
    clock = FakeClock(5.0)
    j = Journal(tmp_path / "log.jsonl", "r", LocalFiles(), clock)
    j.start(EventType.STAGE_STARTED, stage="a")
    clock.advance(2)
    done = j.finish(EventType.STAGE_FINISHED, stage="a")
    assert done["duration_s"] == 2
    orphan = j.finish(EventType.TASK_FINISHED, task_id="X")
    assert orphan["duration_s"] == 0
    events = parse_log((tmp_path / "log.jsonl").read_text())
    assert [e["seq"] for e in events] == [1, 2, 3]


def test_parse_accepts_last_record_without_newline():
    text = '{"schema_version":"1.0","event":"run_started"}'
    assert len(parse_log(text)) == 1


def test_local_files_atomic_write_and_append(tmp_path):
    fs = LocalFiles()
    p = tmp_path / "d" / "state.json"
    fs.write_atomic(p, "one")
    fs.write_atomic(p, "two")
    assert fs.read_text(p) == "two"
    assert [f.name for f in p.parent.iterdir()] == ["state.json"]
    log = tmp_path / "x" / "log.jsonl"
    fs.append_line(log, "a")
    fs.append_line(log, "b\n")
    assert log.read_text() == "a\nb\n"


def test_system_clock_monotonic_non_decreasing():
    c = SystemClock()
    assert c.now() > 0
    assert c.monotonic() <= c.monotonic()
