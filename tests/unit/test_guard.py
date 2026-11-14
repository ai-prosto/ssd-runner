from pathlib import Path

from sprc_runner.core.guard import (
    ViolationKind,
    commit_message,
    evaluate,
    find_secret,
    is_product_path,
    path_allowed,
    significant_shrink,
)
from sprc_runner.ports import DiffEntry, DiffSummary

CHARTER = Path("/work/repo")
TARGET = CHARTER / ".sdd-runs"


def verdict(*entries: DiffEntry, target: Path = TARGET):
    return evaluate(DiffSummary(entries), target, CHARTER)


def kinds(v) -> list[ViolationKind]:
    return [x.kind for x in v.violations]


def test_harmless_change_allowed() -> None:
    v = verdict(
        DiffEntry(str(TARGET / "a.py"), "M", 5, 3, 100, "x = 1"),
        DiffEntry(str(TARGET / "b.py"), "A", 10, 0, 0, "y = 2"),
    )
    assert v.allowed and v.reason == ""


def test_deletion() -> None:
    v = verdict(DiffEntry(str(TARGET / "a.py"), "D", 0, 10, 10))
    assert kinds(v) == [ViolationKind.DELETION]
    assert "a.py" in v.reason


def test_shrink_and_threshold_edges() -> None:
    big = DiffEntry(str(TARGET / "a.py"), "M", 0, 60, 100)
    assert kinds(verdict(big)) == [ViolationKind.SHRINK]
    small_file = DiffEntry(str(TARGET / "a.py"), "M", 0, 10, 12)
    assert not significant_shrink(small_file)  # мало строк
    rewrite = DiffEntry(str(TARGET / "a.py"), "M", 60, 60, 100)
    assert not significant_shrink(rewrite)  # чистой потери нет
    assert not significant_shrink(DiffEntry(str(TARGET / "a.py"), "A", 0, 99, 100))
    assert significant_shrink(small_file, min_removed=5, min_ratio=0.5)


def test_path_verdict_is_conjunction() -> None:
    for rel in ("profiles/x.yaml", "scripts/review/a.sh", "spec-runner.config.yaml"):
        assert not path_allowed(CHARTER / rel, CHARTER, CHARTER)  # target накрывает
        assert not path_allowed(CHARTER / rel, TARGET, CHARTER)
    assert not path_allowed(CHARTER / ".github" / "w.yml", CHARTER, CHARTER)
    assert not path_allowed(CHARTER / "README.md", TARGET, CHARTER)  # вне target
    assert path_allowed(TARGET / "a.py", TARGET, CHARTER)
    assert path_allowed(CHARTER / "app.py", CHARTER, CHARTER)


def test_forbidden_inside_target_violates() -> None:
    v = verdict(
        DiffEntry(str(CHARTER / "profiles/x.yaml"), "M", 1, 1, 5), target=CHARTER
    )
    assert kinds(v) == [ViolationKind.PATH_OUTSIDE]
    v = verdict(DiffEntry(str(CHARTER / "README.md"), "M", 1, 1, 5))
    assert kinds(v) == [ViolationKind.PATH_OUTSIDE]


def test_is_product_path_excludes_service() -> None:
    assert is_product_path(TARGET / "a.py", TARGET, CHARTER)
    assert not is_product_path(TARGET / ".sprc" / "log.jsonl", TARGET, CHARTER)
    assert not is_product_path(CHARTER / "profiles/a", CHARTER, CHARTER)


def test_secret_detected_without_leaking() -> None:
    text = "key = 'AKIAABCDEFGHIJKLMNOP'"
    assert find_secret(text) == "aws_access_key"
    assert find_secret("-----BEGIN RSA PRIVATE KEY-----") == "private_key"
    assert find_secret('password = "hunter2hunter2hunter2"') == "assigned_secret"
    assert find_secret("x = 1") is None
    v = verdict(DiffEntry(str(TARGET / "a.py"), "A", 1, 0, 0, text))
    assert kinds(v) == [ViolationKind.SECRET]
    assert "AKIA" not in v.reason


def test_multiple_violations_all_reported() -> None:
    v = verdict(
        DiffEntry(str(TARGET / "a.py"), "D", 0, 5, 5),
        DiffEntry(str(CHARTER / "profiles/p"), "M", 1, 1, 3),
    )
    assert kinds(v) == [ViolationKind.DELETION, ViolationKind.PATH_OUTSIDE] or set(
        kinds(v)
    ) == {ViolationKind.DELETION, ViolationKind.PATH_OUTSIDE}


def test_commit_message_trailers() -> None:
    msg = commit_message(
        "TASK-001", 2, "Имя", run_id="r1", tests_returncode=0, review_approved=True
    )
    assert msg.splitlines()[0] == "TASK-001 (попытка 2): Имя"
    for line in (
        "Sprc-Task-Id: TASK-001",
        "Sprc-Attempt: 2",
        "Sprc-Run-Id: r1",
        "Sprc-Tests-Returncode: 0",
        "Sprc-Review: approved",
    ):
        assert line in msg.splitlines()
