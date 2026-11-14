"""DEL-09 и DEL-13: чистые функции размещения и `.gitignore` прогона."""

from pathlib import Path

from sprc_runner.core import layout
from sprc_runner.core.prepare import ensure_gitignore, init_run, new_run_id


def test_target_default_and_param(tmp_path: Path) -> None:
    assert layout.resolve_target(None, tmp_path) == (tmp_path / ".sdd-runs").resolve()
    assert layout.resolve_target("o", tmp_path) == (tmp_path / "o").resolve()
    assert (
        layout.resolve_target(tmp_path / "abs", Path("/x"))
        == (tmp_path / "abs").resolve()
    )


def test_run_dir_path(tmp_path: Path) -> None:
    assert layout.run_dir_path(tmp_path, "r1") == tmp_path / ".sprc" / "r1"


def test_classify_paths(tmp_path: Path) -> None:
    k = layout.PathKind
    assert layout.classify_path(tmp_path, Path("src/a.py")) == k.PRODUCT
    assert layout.classify_path(tmp_path, Path(".sprc/r/log")) == k.SERVICE
    assert layout.classify_path(tmp_path, tmp_path / ".sprc") == k.SERVICE
    assert layout.classify_path(tmp_path, tmp_path.parent / "o") == k.OUTSIDE
    assert layout.classify_path(tmp_path, Path("../o")) == k.OUTSIDE


def test_forbidden_paths_relative_to_charter_root(tmp_path: Path) -> None:
    paths = layout.forbidden_paths(tmp_path)
    assert tmp_path.resolve() / "scripts" / "review" in paths
    assert len(paths) == 4
    assert layout.is_forbidden(tmp_path / "profiles" / "a.yaml", tmp_path)
    assert layout.is_forbidden(tmp_path / ".github", tmp_path)
    assert not layout.is_forbidden(tmp_path / "scripts" / "other", tmp_path)
    assert not layout.is_forbidden(tmp_path / "src" / "a.py", tmp_path)


def test_gitignore_created_appended_idempotent(tmp_path: Path) -> None:
    ensure_gitignore(tmp_path)
    ensure_gitignore(tmp_path)
    assert (tmp_path / ".gitignore").read_text() == ".sprc/\n"
    other = tmp_path / "o"
    other.mkdir()
    (other / ".gitignore").write_text("a")
    ensure_gitignore(other)
    assert (other / ".gitignore").read_text() == "a\n.sprc/\n"


def test_init_run_copies_input_and_ignores_service_dir(tmp_path: Path) -> None:
    run_dir = init_run(tmp_path, "r1", b"abc")
    assert run_dir == tmp_path / ".sprc" / "r1"
    assert (run_dir / "input.md").read_bytes() == b"abc"
    assert ".sprc/" in (tmp_path / ".gitignore").read_text().splitlines()


def test_run_ids_unique() -> None:
    assert new_run_id() != new_run_id()
