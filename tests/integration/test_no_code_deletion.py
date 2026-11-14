"""BEH-32: готовый код предыдущих задач не удаляется последующими."""

from pathlib import Path

from tests.git_helpers import attempt, git, make_repo, write

CODE = "".join(f"def f{i}(): return {i}\n" for i in range(60))


def seeded(tmp_path: Path) -> tuple[Path, Path]:
    repo = make_repo(tmp_path / "repo")
    target = repo / ".sdd-runs"
    write(target / "mod" / "a.py", CODE)
    write(target / "mod" / "b.py", CODE)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "ранние задачи")
    return repo, target


def head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").strip()


def test_deleted_file_is_detected_before_commit_and_restored(tmp_path: Path) -> None:
    repo, target = seeded(tmp_path)
    before = head(repo)
    record, events, _ = attempt(repo, target, (target / "mod" / "a.py").unlink)
    assert head(repo) == before and not record.success
    verdict = [e for e in events if e["event"] == "diff_verdict"][0]
    assert verdict["violations"][0]["kind"] == "deletion"
    assert (target / "mod" / "a.py").read_text(encoding="utf-8") == CODE
    assert not git(repo, "status", "--porcelain").strip()


def test_significant_shrink_is_detected(tmp_path: Path) -> None:
    repo, target = seeded(tmp_path)
    before = head(repo)
    record, events, _ = attempt(
        repo, target, lambda: write(target / "mod" / "b.py", "def f0(): return 0\n")
    )
    assert head(repo) == before and not record.success
    kinds = [
        v["kind"]
        for e in events
        if e["event"] == "diff_verdict"
        for v in e["violations"]
    ]
    assert kinds == ["shrink"]
    assert (target / "mod" / "b.py").read_text(encoding="utf-8") == CODE


def test_unrelated_new_file_keeps_old_code_and_commits(tmp_path: Path) -> None:
    repo, target = seeded(tmp_path)
    before = head(repo)
    record, _, _ = attempt(
        repo, target, lambda: write(target / "mod" / "c.py", "c = 1\n")
    )
    assert record.success and head(repo) != before
    assert (target / "mod" / "a.py").read_text(encoding="utf-8") == CODE
    assert (target / "mod" / "b.py").read_text(encoding="utf-8") == CODE


def test_small_edit_of_old_file_is_harmless(tmp_path: Path) -> None:
    repo, target = seeded(tmp_path)
    record, _, _ = attempt(
        repo, target, lambda: write(target / "mod" / "a.py", CODE + "# done\n")
    )
    assert record.success
