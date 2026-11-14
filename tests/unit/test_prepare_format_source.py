"""Регрессия живого прогона 2026-11-14: источник формата должен доходить
до агента, а текст отказа не должен засчитываться за артефакт подготовки."""

import asyncio
from pathlib import Path

import pytest

from sprc_runner.config import load_config
from sprc_runner.core.prepare import PrepareError, init_run, run_preparation
from sprc_runner.doubles import EchoAgent, FakeAgent
from sprc_runner.ports import Outcome, StageResult

ROOT = Path(__file__).resolve().parents[2]


def _run_dir(tmp_path: Path) -> Path:
    return init_run(tmp_path / "t", "run-1", "описание".encode())


def test_format_source_body_reaches_every_stage(tmp_path: Path) -> None:
    config = load_config(ROOT / "sprc-runner.toml")
    agent = EchoAgent()
    run_dir = _run_dir(tmp_path)
    asyncio.run(run_preparation(agent, config, run_dir, format_root=ROOT))
    body = (ROOT / config.format_skill_path).read_text(encoding="utf-8")
    assert agent.invocations == 3
    for call in agent.calls:
        assert body in call.inputs[0], call.stage  # содержимое, а не только ссылка


def test_missing_format_source_refuses_before_invocations(tmp_path: Path) -> None:
    config = load_config(ROOT / "sprc-runner.toml")
    agent = EchoAgent()
    run_dir = _run_dir(tmp_path)
    with pytest.raises(PrepareError) as err:
        asyncio.run(
            run_preparation(agent, config, run_dir, format_root=tmp_path / "нет")
        )
    assert agent.invocations == 0  # отказ до единой инвокации — платных вызовов нет
    assert "источник формата недоступен" in str(err.value.result.stop_reason)


def test_refusal_text_is_not_an_artifact(tmp_path: Path) -> None:
    config = load_config(ROOT / "sprc-runner.toml")
    refusal = StageResult(
        Outcome.DONE,
        "Файла формата нет, а структуру артефакта я выдумывать не буду.",
        None,
        "end_turn",
    )
    agent = FakeAgent([refusal])
    run_dir = _run_dir(tmp_path)
    with pytest.raises(PrepareError) as err:
        asyncio.run(run_preparation(agent, config, run_dir))
    assert err.value.stage == "prepare_spec"
    assert "нет ни одного заголовка" in str(err.value.result.stop_reason)
    # текст отказа остаётся на диске как эвиденция, но этап не засчитан
    assert (run_dir / "spec.md").read_text(encoding="utf-8")
