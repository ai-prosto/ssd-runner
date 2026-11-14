"""BEH-04: применённая модель каждого этапа и попытки видна в логе."""

from pathlib import Path

from sprc_runner.adapters.fs import LocalFiles
from sprc_runner.config import load_config
from sprc_runner.core.logbook import EventType, Journal, read_log
from sprc_runner.core.prepare import run_preparation
from sprc_runner.doubles import EchoAgent, FakeClock

ROOT = Path(__file__).resolve().parents[2]


def test_attempt_and_step_events_carry_configured_model(tmp_path, run_logged):
    config, _, events, _ = run_logged(tmp_path, [1, 0, 0])
    expected = {
        "implement": config.model_implement,
        "review": config.model_review,
    }
    attempts = [e for e in events if e["event"] == "attempt_finished"]
    assert len(attempts) == 3
    assert all(a["model"] == config.model_implement for a in attempts)
    for step in (e for e in events if e["event"] == "step_finished"):
        if step["stage"] in expected:
            assert step["model"] == expected[step["stage"]]


def test_prepare_stages_carry_configured_model(tmp_path):
    config = load_config(ROOT / "sprc-runner.toml")
    run_dir = tmp_path
    (run_dir / "input.md").write_text("описание", encoding="utf-8")
    path = run_dir / "log.jsonl"
    journal = Journal(path, "r", LocalFiles(), FakeClock())
    import asyncio

    asyncio.run(run_preparation(EchoAgent(), config, run_dir, journal))
    stages = {
        e["stage"]: e["model"]
        for e in read_log(path, LocalFiles())
        if e["event"] == EventType.STAGE_FINISHED
    }
    assert stages == {
        "prepare_spec": config.model_prepare,
        "prepare_requirements": config.model_prepare,
        "prepare_tasks": config.model_tasks,
    }
