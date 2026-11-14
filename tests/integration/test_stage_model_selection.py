"""BEH-01: модель этапа берётся из конфигурации и применяется к этапу."""

from collections.abc import Callable
from pathlib import Path

from sprc_runner.cli import main
from sprc_runner.doubles import EchoAgent


def _run(cfg: Path, cwd: Path) -> EchoAgent:
    agent = EchoAgent()
    assert main(["run", "описание", "--config", str(cfg)], cwd=cwd, agent=agent) == 0
    return agent


def test_each_stage_uses_its_own_model(
    tmp_path: Path, config_file: Path, set_models: Callable[..., None]
) -> None:
    set_models(model_prepare="m-prep", model_tasks="m-tasks")
    agent = _run(config_file, tmp_path)
    models = {c.stage: c.model for c in agent.calls}
    assert models == {
        "prepare_spec": "m-prep",
        "prepare_requirements": "m-prep",
        "prepare_tasks": "m-tasks",
    }


def test_changing_one_key_changes_only_its_stage(
    tmp_path: Path, config_file: Path, set_models: Callable[..., None]
) -> None:
    set_models(model_prepare="a", model_tasks="b")
    before = {c.stage: c.model for c in _run(config_file, tmp_path).calls}
    set_models(model_tasks="changed")
    after = {c.stage: c.model for c in _run(config_file, tmp_path).calls}
    assert after["prepare_tasks"] == "changed"
    assert after["prepare_spec"] == before["prepare_spec"] == "a"
    assert after["prepare_requirements"] == before["prepare_requirements"] == "a"
