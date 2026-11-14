"""Чистые переходы состояния прогона и сериализация."""

import json

import pytest

from sprc_runner.core import state as st


def base() -> st.RunState:
    s = st.new_state(
        "r1", "abc", {"max_attempts": 3}, ["prepare_spec", "prepare_tasks"]
    )
    return st.register_tasks(s, ["T-1", "T-2", "T-3"])


def test_new_state_shape() -> None:
    s = st.new_state("r1", "abc", {}, ["a", "b"])
    assert [x.status for x in s.stages] == [st.StageStatus.PENDING] * 2
    assert s.tasks == () and s.cursor is None and not s.finished
    assert s.schema_version == st.STATE_SCHEMA_VERSION


def test_finish_stage_records_model_and_adds_unknown() -> None:
    s = st.finish_stage(base(), "prepare_spec", "m1", True)
    assert s.stages[0].status is st.StageStatus.DONE and s.stages[0].model == "m1"
    s = st.finish_stage(s, "extra", "m2", False)
    assert s.stages[-1] == st.StageState("extra", st.StageStatus.FAILED, "m2")


def test_task_transitions() -> None:
    s = st.take_task(base(), "T-1")
    assert s.tasks[0].status is st.TaskStatus.IN_PROGRESS
    assert s.cursor == st.Cursor("T-1")
    s = st.start_attempt(s, "T-1", 1)
    assert s.cursor == st.Cursor("T-1", 1)
    s = st.finish_attempt(s, "T-1", 1, False, "tests_failed")
    assert s.tasks[0].attempts == 1
    assert s.tasks[0].last_attempt == st.AttemptState(1, False, "tests_failed")
    s = st.finish_attempt(s, "T-1", 2, True, None, "c0ffee")
    assert s.tasks[0].commit == "c0ffee" and s.tasks[0].attempts == 2
    s = st.finish_attempt(s, "T-1", 3, False, "x")
    assert s.tasks[0].commit == "c0ffee"
    s = st.finish_task(s, "T-1", True)
    assert s.tasks[0].status is st.TaskStatus.DONE and s.cursor is None


def test_uncounted_attempt_does_not_increase_attempts() -> None:
    s = st.finish_attempt(base(), "T-1", 1, False, "limit", counted=False)
    assert s.tasks[0].attempts == 0
    assert s.tasks[0].last_attempt is not None


def test_failed_task_status() -> None:
    s = st.finish_task(st.take_task(base(), "T-1"), "T-1", False)
    assert s.tasks[0].status is st.TaskStatus.FAILED


def test_unknown_task_rejected() -> None:
    with pytest.raises(st.StateFormatError):
        st.take_task(base(), "nope")
    with pytest.raises(st.StateFormatError):
        st.finish_attempt(base(), "nope", 1, True)


def test_next_unfinished_skips_done_and_failed() -> None:
    s = base()
    assert st.next_unfinished(s).task_id == "T-1"  # type: ignore[union-attr]
    s = st.finish_task(s, "T-1", True)
    s = st.finish_task(s, "T-2", False)
    assert st.next_unfinished(s).task_id == "T-3"  # type: ignore[union-attr]
    s = st.take_task(s, "T-3")
    assert st.next_unfinished(s).task_id == "T-3"  # type: ignore[union-attr]
    assert st.unfinished_ids(s) == ["T-3"]
    s = st.finish_task(s, "T-3", True)
    assert st.next_unfinished(s) is None
    assert st.done_count(s) == 2


def test_pause_finish_resume() -> None:
    s = st.pause(st.take_task(base(), "T-1"), st.REASON_INTERRUPTED)
    assert not s.finished and s.reason == "interrupted"
    s = st.finish_run(s, "partial")
    assert s.finished and s.cursor is None
    s = st.resume_run(s, {"max_attempts": 5})
    assert not s.finished and s.reason is None and s.config == {"max_attempts": 5}


def test_reconcile_marks_closed_tasks_done() -> None:
    s = st.reconcile(st.take_task(base(), "T-1"), ["T-1", "T-3"])
    assert [t.status for t in s.tasks] == [
        st.TaskStatus.DONE,
        st.TaskStatus.PENDING,
        st.TaskStatus.DONE,
    ]
    assert st.reconcile(s, ["T-1"]) == s


def test_roundtrip() -> None:
    s = st.finish_stage(base(), "prepare_spec", "m", True)
    s = st.finish_attempt(st.start_attempt(s, "T-2", 1), "T-2", 1, True, None, "abc")
    s = st.pause(s, "interrupted")
    text = st.encode_state(s)
    assert text.endswith("\n")
    assert st.decode_state(text) == s


def doc() -> dict:
    return json.loads(st.encode_state(base()))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.pop("run_id"),
        lambda d: d.update(schema_version="2.0"),
        lambda d: d.update(schema_version=1),
        lambda d: d["tasks"][0].update(status="weird"),
        lambda d: d["tasks"][0].update(attempts="1"),
        lambda d: d["tasks"][0].update(attempts=True),
        lambda d: d["tasks"][0].update(commit=5),
        lambda d: d["tasks"][0].update(last_attempt={"number": 1}),
        lambda d: d["stages"][0].update(status="running"),
        lambda d: d.update(cursor={"task_id": "ghost", "attempt": None}),
        lambda d: d.update(cursor={"task_id": "T-1", "attempt": "1"}),
        lambda d: d.update(finished="no"),
        lambda d: d.update(reason=3),
        lambda d: d["tasks"].append(dict(d["tasks"][0])),
        lambda d: d.update(tasks="x"),
    ],
)
def test_decode_rejects_schema_violations(mutate) -> None:
    d = doc()
    mutate(d)
    with pytest.raises(st.StateFormatError):
        st.decode_state(json.dumps(d))


def test_decode_rejects_non_json_and_non_object() -> None:
    with pytest.raises(st.StateFormatError):
        st.decode_state("{oops")
    with pytest.raises(st.StateFormatError):
        st.decode_state("[]")
