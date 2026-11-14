"""BEH-33: прогон не изменяет файлы вне своих каталогов (CON-07)."""

from pathlib import Path

import pytest

from tests.git_helpers import CON07_FILES, attempt, git, make_repo, write


def snapshot(repo: Path) -> dict[str, str]:
    return {rel: (repo / rel).read_text(encoding="utf-8") for rel in CON07_FILES}


def committed_paths(repo: Path, since: str) -> set[str]:
    out = git(repo, "log", f"{since}..HEAD", "--name-only", "--format=")
    return set(out.split())


@pytest.mark.parametrize("target_rel", [".sdd-runs", "."])
def test_successful_run_leaves_con07_untouched(tmp_path: Path, target_rel: str) -> None:
    repo = make_repo(tmp_path / "repo")
    target = (repo / target_rel).resolve()
    before, snap = git(repo, "rev-parse", "HEAD").strip(), snapshot(repo)
    record, _, _ = attempt(
        repo, target, lambda: write(target / "app" / "n.py", "n=1\n")
    )
    assert record.success
    assert snapshot(repo) == snap
    assert not committed_paths(repo, before) & set(CON07_FILES)


@pytest.mark.parametrize("rel", CON07_FILES)
def test_agent_touching_con07_covered_by_target_is_refused(
    tmp_path: Path, rel: str
) -> None:
    repo = make_repo(tmp_path / "repo")
    before = git(repo, "rev-parse", "HEAD").strip()
    record, events, _ = attempt(
        repo,
        repo,
        lambda: (
            write(repo / rel, "tampered\n"),
            write(repo / "app" / "n.py", "n=1\n"),
        ),
    )
    assert not record.success
    assert git(repo, "rev-parse", "HEAD").strip() == before  # ни одного коммита
    assert any(
        v["kind"] == "path_outside" and v["path"].endswith(rel)
        for e in events
        if e["event"] == "diff_verdict"
        for v in e["violations"]
    )
    # откат касается продуктовых путей, но не индексирует и не коммитит запретные
    assert rel not in git(repo, "diff", "--cached", "--name-only").split()


def test_stage_paths_never_indexes_con07(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    _, _, adapter = attempt(repo, repo, lambda: None)
    for rel in CON07_FILES:
        write(repo / rel, "tampered\n")
    write(repo / "app" / "ok.py", "ok\n")
    adapter.stage_paths(repo, [*CON07_FILES, "app/ok.py", str(repo.parent / "x")])
    assert git(repo, "diff", "--cached", "--name-only").split() == ["app/ok.py"]


def test_failed_attempt_does_not_touch_files_outside(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    target = repo / ".sdd-runs"
    write(repo / "notes.txt", "mine\n")  # посторонний неотслеживаемый файл
    record, _, _ = attempt(
        repo, target, lambda: write(target / "secret.py", "K='AKIAABCDEFGHIJKLMNOP'\n")
    )
    assert not record.success
    assert (repo / "notes.txt").read_text(encoding="utf-8") == "mine\n"
    assert not (target / "secret.py").exists()
    assert (target / ".sprc" / "run-1" / "log.jsonl").exists()  # .sprc/ пережил откат
    assert committed_paths(repo, "HEAD") == set()


def test_aborted_run_with_exception_leaves_con07_untouched(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    snap = snapshot(repo)

    def crash() -> None:
        raise RuntimeError("авария")

    with pytest.raises(RuntimeError):
        attempt(repo, repo / ".sdd-runs", crash)
    assert snapshot(repo) == snap
    assert git(repo, "diff", "--cached", "--name-only").strip() == ""
