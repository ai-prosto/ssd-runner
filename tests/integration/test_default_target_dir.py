"""BEH-13: без параметра результат кладётся в `.sdd-runs/` каталога запуска."""

from pathlib import Path

from sprc_runner.cli import main
from sprc_runner.doubles import EchoAgent


def test_default_target_and_foreign_files_untouched(
    tmp_path: Path, config_file: Path
) -> None:
    (tmp_path / "foreign.txt").write_text("чужой", encoding="utf-8")
    (tmp_path / "dir").mkdir()
    (tmp_path / "dir" / "x.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("mine\n", encoding="utf-8")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert (
        main(
            ["run", "текст", "--config", str(config_file)],
            cwd=tmp_path,
            agent=EchoAgent(),
        )
        == 0
    )
    target = tmp_path / ".sdd-runs"
    assert len(list((target / ".sprc").iterdir())) == 1
    after = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    new = set(after) - set(before)
    assert new and all(target in p.parents for p in new)
    assert all(after[p] == data for p, data in before.items())


def test_explicit_target_param(tmp_path: Path, config_file: Path) -> None:
    out = tmp_path / "custom"
    main(
        ["run", "t", "--target", str(out), "--config", str(config_file)],
        cwd=tmp_path,
        agent=EchoAgent(),
    )
    assert (out / ".sprc").is_dir()
    assert not (tmp_path / ".sdd-runs").exists()
