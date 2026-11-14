"""Сторож диффа: чистые вердикты над структурированной сводкой диффа.

Закрытый набор нарушений: удаление файла, значимое сокращение содержимого,
выход за разрешённые пути, присутствие секрета (Q-09). Допустимость пути —
конъюнкция «внутри `<target>`» и «не в перечне CON-07» (Q-11). Модуль не
обращается к файловой системе, git и сети: вход — `DiffSummary` (BEH-32..34).
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from sprc_runner.core.layout import PathKind, classify_path, is_forbidden
from sprc_runner.ports import DiffEntry, DiffSummary

SHRINK_MIN_REMOVED_LINES = 20
SHRINK_MIN_RATIO = 0.5
"""Порог «значимого сокращения»: чистая потеря не меньше стольких строк и доли файла.

ВНИМАНИЕ: в соседнем репозитории spec-runner такого порога найти не удалось
(поиск по `src`, `docs`, конфигурации), поэтому значения — временные; перенос
значением из spec-runner (NFR-02) остаётся открытым пунктом и делается правкой
этих двух констант, а не логики.
"""


class ViolationKind(StrEnum):
    """Закрытый набор нарушений диффа."""

    DELETION = "deletion"
    SHRINK = "shrink"
    PATH_OUTSIDE = "path_outside"
    SECRET = "secret"


@dataclass(frozen=True)
class Violation:
    """Одно нарушение; `detail` никогда не содержит текст секрета."""

    kind: ViolationKind
    path: str
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind.value, "path": self.path, "detail": self.detail}


@dataclass(frozen=True)
class Verdict:
    """Вердикт сторожа: допустим тогда и только тогда, когда нарушений нет."""

    violations: tuple[Violation, ...] = ()

    @property
    def allowed(self) -> bool:
        return not self.violations

    @property
    def reason(self) -> str:
        return "; ".join(
            f"{v.kind.value}: {v.path}" + (f" ({v.detail})" if v.detail else "")
            for v in self.violations
        )


SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    (
        "assigned_secret",
        re.compile(
            r"(?i)\b(api[_-]?key|secret|token|passw(?:or)?d)\b\s*[:=]\s*"
            r"[\"'][^\"'\s]{12,}[\"']"
        ),
    ),
)


def path_allowed(path: str | Path, target: Path, charter_root: Path) -> bool:
    """Вердикт о пути: внутри `<target>` И не в перечне CON-07."""
    inside = classify_path(target, Path(path)) is not PathKind.OUTSIDE
    return inside and not is_forbidden(Path(path), charter_root)


def is_product_path(path: str | Path, target: Path, charter_root: Path) -> bool:
    """Путь, который можно индексировать: продуктовый и допустимый."""
    return classify_path(target, Path(path)) is PathKind.PRODUCT and path_allowed(
        path, target, charter_root
    )


def significant_shrink(
    entry: DiffEntry,
    min_removed: int = SHRINK_MIN_REMOVED_LINES,
    min_ratio: float = SHRINK_MIN_RATIO,
) -> bool:
    """Значимо ли сократилось содержимое изменённого файла."""
    if entry.status != "M" or entry.lines_before <= 0:
        return False
    loss = entry.removed - entry.added
    return loss >= min_removed and loss / entry.lines_before >= min_ratio


def find_secret(text: str) -> str | None:
    """Вид первого найденного секрета в тексте либо `None`."""
    for kind, pattern in SECRET_PATTERNS:
        if pattern.search(text):
            return kind
    return None


def evaluate(
    summary: DiffSummary,
    target: Path,
    charter_root: Path,
    *,
    min_removed: int = SHRINK_MIN_REMOVED_LINES,
    min_ratio: float = SHRINK_MIN_RATIO,
) -> Verdict:
    """Вердикт по сводке диффа: все нарушения закрытого набора, по порядку записей."""
    found: list[Violation] = []
    for entry in summary.entries:
        if not path_allowed(entry.path, target, charter_root):
            found.append(Violation(ViolationKind.PATH_OUTSIDE, entry.path))
        if entry.status == "D":
            found.append(Violation(ViolationKind.DELETION, entry.path))
        elif significant_shrink(entry, min_removed, min_ratio):
            found.append(
                Violation(
                    ViolationKind.SHRINK,
                    entry.path,
                    f"-{entry.removed}/+{entry.added} из {entry.lines_before}",
                )
            )
        secret = find_secret(entry.added_text)
        if secret:
            found.append(Violation(ViolationKind.SECRET, entry.path, secret))
    return Verdict(tuple(found))


def commit_message(
    task_id: str,
    attempt: int,
    title: str,
    *,
    run_id: str,
    tests_returncode: int | None,
    review_approved: bool,
) -> str:
    """Сообщение коммита задачи: заголовок и машиночитаемые трейлеры."""
    return (
        f"{task_id} (попытка {attempt}): {title}\n\n"
        f"Sprc-Task-Id: {task_id}\n"
        f"Sprc-Attempt: {attempt}\n"
        f"Sprc-Run-Id: {run_id}\n"
        f"Sprc-Tests-Returncode: {tests_returncode}\n"
        f"Sprc-Review: {'approved' if review_approved else 'rejected'}\n"
    )
