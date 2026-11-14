"""BEH-26: один читатель разбирает логи прогонов с разными моделями."""

from pathlib import Path

import pytest

from sprc_runner.adapters.fs import LocalFiles
from sprc_runner.core.logbook import (
    SCHEMA_VERSION,
    EventType,
    Journal,
    LogFormatError,
    parse_log,
    read_log,
)
from sprc_runner.doubles import FakeClock

ROOT = Path(__file__).resolve().parents[2]


def _run(path, run_id, model):
    journal = Journal(path, run_id, LocalFiles(), FakeClock(10.0))
    journal.start(EventType.RUN_STARTED)
    journal.emit(EventType.STAGE_STARTED, stage="implement", model=model)
    journal.start(EventType.TASK_STARTED, task_id="TASK-001")
    journal.finish(EventType.TASK_FINISHED, task_id="TASK-001")
    journal.finish(EventType.RUN_FINISHED)


def test_two_runs_with_different_models_parse_with_same_reader(tmp_path):
    _run(tmp_path / "a.jsonl", "run-a", "model-one")
    _run(tmp_path / "b.jsonl", "run-b", "model-two")
    logs = [read_log(tmp_path / n, LocalFiles()) for n in ("a.jsonl", "b.jsonl")]
    for events in logs:
        assert events
        assert all(e["schema_version"] == SCHEMA_VERSION for e in events)
    models = [e["model"] for events in logs for e in events if "model" in e]
    assert models == ["model-one", "model-two"]


def test_schema_document_in_repo_names_version():
    doc = (ROOT / "schemas" / "log-schema.md").read_text(encoding="utf-8")
    assert f"Версия схемы: {SCHEMA_VERSION}" in doc
    for kind in EventType:
        assert kind.value in doc


def test_minor_extension_accepted_foreign_major_rejected():
    ok = '{"schema_version":"1.7","event":"run_started","extra":1}\n'
    assert parse_log(ok)[0]["extra"] == 1
    for bad in (
        '{"schema_version":"2.0","event":"run_started"}\n',
        '{"event":"run_started"}\n',
        '{"schema_version":"1.0","event":"nope"}\n',
        "[1]\n",
        "garbage\n{}\n",
    ):
        with pytest.raises(LogFormatError):
            parse_log(bad)
