"""Регрессия живого прогона 2026-11-14: целевой каталог вне git-репозитория.

Дифф-сторож и коммит попытки требуют репозитория (NFR-02, NFR-05); свежий
POC-каталог им не является, и прогон падал на `rev-parse --show-toplevel`.
"""

import subprocess
from pathlib import Path

from sprc_runner.adapters.git import ensure_repo


def _git(cwd: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )
    return out.stdout.strip()


def test_ensure_repo_initialises_bare_directory(tmp_path: Path) -> None:
    target = tmp_path / "poc"
    target.mkdir()
    ensure_repo(target)
    assert (target / ".git").is_dir()
    # начальный коммит существует: точка отката первой попытки
    assert _git(target, "rev-parse", "--verify", "HEAD")
    assert "начальная точка прогона" in _git(target, "log", "--format=%s")


def test_ensure_repo_leaves_existing_repo_alone(tmp_path: Path) -> None:
    target = tmp_path / "repo"
    target.mkdir()
    _git(tmp_path / "repo", "init", "-q")
    _git(target, "config", "user.name", "t")
    _git(target, "config", "user.email", "t@t")
    (target / "a.txt").write_text("a", encoding="utf-8")
    _git(target, "add", "-A")
    _git(target, "commit", "-q", "-m", "their-commit")
    head = _git(target, "rev-parse", "HEAD")
    ensure_repo(target)
    assert _git(target, "rev-parse", "HEAD") == head  # ни коммитов, ни init
    assert _git(target, "log", "--format=%s") == "their-commit"


def test_ensure_repo_respects_enclosing_repo(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    inner = tmp_path / "sub" / "target"
    inner.mkdir(parents=True)
    ensure_repo(inner)
    # каталог внутри существующего репозитория не превращается во вложенный
    assert not (inner / ".git").exists()
