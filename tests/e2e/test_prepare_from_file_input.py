"""BEH-08: описание из файла даёт тот же дальнейший сценарий."""

from pathlib import Path

from sprc_runner.cli import main
from sprc_runner.doubles import EchoAgent

TEXT = "Нужен сервис.\n\nФункции, ограничения, критерии успеха.\n"


def _artifacts(args: list[str], cwd: Path, cfg: Path) -> tuple[list[str], dict]:
    agent = EchoAgent()
    assert main([*args, "--config", str(cfg)], cwd=cwd, agent=agent) == 0
    (run_dir,) = (cwd / ".sdd-runs" / ".sprc").iterdir()
    files = {p.name: p.read_text(encoding="utf-8") for p in run_dir.glob("*.md")}
    return [c.stage for c in agent.calls], files


def test_file_gives_same_scenario_as_text(tmp_path: Path, config_file: Path) -> None:
    src = tmp_path / "desc.md"
    src.write_text(TEXT, encoding="utf-8")
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    stages_f, files_f = _artifacts(["run", "--file", str(src)], a, config_file)
    stages_t, files_t = _artifacts(["run", TEXT], b, config_file)
    assert stages_f == stages_t
    assert files_f == files_t
    assert set(files_f) == {"input.md", "spec.md", "requirements.md", "tasks.md"}


def test_missing_file_refused(tmp_path: Path, config_file: Path) -> None:
    code = main(
        ["run", "--file", str(tmp_path / "none"), "--config", str(config_file)],
        cwd=tmp_path,
        agent=EchoAgent(),
    )
    assert code == 4
