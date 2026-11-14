from pathlib import Path

import anyio
import pytest

from sprc_runner.cli import main
from sprc_runner.config import load_config
from sprc_runner.core.prepare import PrepareError, init_run, run_preparation
from sprc_runner.doubles import FakeAgent, limit_result


def test_stage_failure_raises(tmp_path: Path) -> None:
    cfg = load_config(Path(__file__).resolve().parents[2] / "sprc-runner.toml")
    run_dir = init_run(tmp_path, "r", b"x")
    agent = FakeAgent([limit_result()])
    with pytest.raises(PrepareError):
        anyio.run(run_preparation, agent, cfg, run_dir)
    assert not (run_dir / "spec.md").exists()


def test_cli_failure_exit_code(tmp_path: Path, config_file: Path) -> None:
    code = main(
        ["run", "t", "--config", str(config_file)],
        cwd=tmp_path,
        agent=FakeAgent([limit_result()]),
    )
    assert code == 3


def test_cli_bad_config_and_usage(tmp_path: Path) -> None:
    assert main(["run", "t", "--config", "nope.toml"], cwd=tmp_path) == 4
    with pytest.raises(SystemExit) as exc:
        main(["run"])
    assert exc.value.code == 4
