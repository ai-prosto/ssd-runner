"""BEH-34: дифф проверяется до коммита, нарушение останавливает фиксацию."""

from pathlib import Path

from tests.git_helpers import attempt, git, make_repo, write


def head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").strip()


def verdicts(events: list[dict]) -> list[dict]:
    return [e for e in events if e["event"] == "diff_verdict"]


def test_harmless_change_is_committed_with_trailers(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    target = repo / ".sdd-runs"
    before = head(repo)
    record, events, _ = attempt(
        repo, target, lambda: write(target / "new.py", "x = 1\n")
    )
    assert record.success and record.commit == head(repo) != before
    body = git(repo, "log", "-1", "--format=%B")
    assert body.startswith("TASK-001 (попытка 1): Первая")
    assert "Sprc-Task-Id: TASK-001" in body and "Sprc-Attempt: 1" in body
    assert git(repo, "show", "--name-only", "--format=", "HEAD").split() == [
        ".sdd-runs/new.py"
    ]
    assert verdicts(events)[0]["allowed"] is True
    assert not git(repo, "status", "--porcelain", "--untracked-files=all").strip()


def test_secret_stops_commit_and_is_logged(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    target = repo / ".sdd-runs"
    before = head(repo)
    record, events, _ = attempt(
        repo, target, lambda: write(target / "k.py", "K = 'AKIAABCDEFGHIJKLMNOP'\n")
    )
    assert head(repo) == before
    assert not record.success and record.error_code == "diff_guard_rejected"
    v = verdicts(events)[0]
    assert v["allowed"] is False and "secret" in v["reason"]
    assert "AKIAABCDEFGHIJKLMNOP" not in str(events)
    finished = [e for e in events if e["event"] == "attempt_finished"][0]
    assert (
        finished["success"] is False and finished["error_code"] == "diff_guard_rejected"
    )
    assert not (target / "k.py").exists()  # мусор попытки убран откатом


def test_path_from_con07_inside_target_stops_commit(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    before = head(repo)
    record, events, _ = attempt(
        repo,
        repo,  # <target> накрывает файлы DarkFactory
        lambda: (
            write(repo / "profiles" / "roles.yaml", "hacked\n"),
            write(repo / "app" / "ok.py", "ok = 1\n"),
        ),
    )
    assert head(repo) == before and not record.success
    assert "path_outside" in verdicts(events)[0]["reason"]


def test_path_outside_target_stops_commit(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    before = head(repo)
    record, events, _ = attempt(
        repo, repo / ".sdd-runs", lambda: write(repo / "README.md", "changed\n")
    )
    assert head(repo) == before and not record.success
    assert verdicts(events)[0]["violations"][0]["kind"] == "path_outside"


def test_discarded_diff_saved_as_evidence(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    target = repo / ".sdd-runs"
    write(target / "app.py", "".join(f"l{i}\n" for i in range(30)))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed")
    record, _, _ = attempt(repo, target, (target / "app.py").unlink, number=2)
    assert not record.success
    diff = target / ".sprc" / "run-1" / "evidence" / "TASK-001-attempt-2.diff"
    assert "app.py" in diff.read_text(encoding="utf-8")
    assert (target / "app.py").exists()  # откат вернул файл
