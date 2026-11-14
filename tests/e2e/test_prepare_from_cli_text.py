"""BEH-07: из описания в командной строке получаются три артефакта подготовки."""

from pathlib import Path

from sprc_runner.cli import main
from sprc_runner.doubles import EchoAgent

TEXT = "Нужен сервис. Он принимает файлы. Он считает хэши."


def run_dir_of(target: Path) -> Path:
    (run_dir,) = (target / ".sprc").iterdir()
    return run_dir


def test_three_artifacts_chained(tmp_path: Path, config_file: Path) -> None:
    agent = EchoAgent()
    code = main(["run", TEXT, "--config", str(config_file)], cwd=tmp_path, agent=agent)
    assert code == 0
    run_dir = run_dir_of(tmp_path / ".sdd-runs")
    spec = (run_dir / "spec.md").read_text(encoding="utf-8")
    req = (run_dir / "requirements.md").read_text(encoding="utf-8")
    tasks = (run_dir / "tasks.md").read_text(encoding="utf-8")
    assert TEXT in spec
    assert spec in req
    assert spec in tasks and req in tasks
    assert [c.stage for c in agent.calls] == [
        "prepare_spec",
        "prepare_requirements",
        "prepare_tasks",
    ]
    assert agent.closed
