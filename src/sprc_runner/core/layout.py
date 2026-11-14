"""Размещение прогона: целевой каталог, каталог прогона, виды путей, CON-07.

Чистые функции без обращения к файловой системе (кроме разрешения путей):
единственное место, где вычисляются целевой каталог, каталог прогона,
разделение путей на продуктовые и служебные и перечень безусловно
запрещённых путей CON-07 (§3.2 дизайна).
"""

from enum import StrEnum
from pathlib import Path

DEFAULT_TARGET_NAME = ".sdd-runs"
"""Целевой каталог по умолчанию внутри каталога запуска (FR-08, BEH-13)."""

SERVICE_DIR_NAME = ".sprc"
"""Имя служебного каталога внутри `<target>`; в `.gitignore` как `.sprc/`."""

FORBIDDEN_RELATIVE = (
    "profiles",
    "scripts/review",
    "spec-runner.config.yaml",
    ".github",
)
"""Перечень CON-07: файлы DarkFactory, недопустимые для записи при любом `<target>`."""


class PathKind(StrEnum):
    """Вид пути относительно целевого каталога."""

    PRODUCT = "product"
    SERVICE = "service"
    OUTSIDE = "outside"


def resolve_target(
    param: str | Path | None, cwd: Path, default: str = DEFAULT_TARGET_NAME
) -> Path:
    """Целевой каталог: параметр команды либо `<cwd>/.sdd-runs/`."""
    path = Path(default if param is None else param)
    return (path if path.is_absolute() else cwd / path).resolve()


def service_dir(target: Path, name: str = SERVICE_DIR_NAME) -> Path:
    """Служебный каталог `<target>/.sprc/`."""
    return target / name


def run_dir_path(target: Path, run_id: str, name: str = SERVICE_DIR_NAME) -> Path:
    """Каталог прогона `<target>/.sprc/<run_id>/`."""
    return service_dir(target, name) / run_id


def gitignore_entry(name: str = SERVICE_DIR_NAME) -> str:
    """Строка `.gitignore`, исключающая служебный каталог."""
    return f"{name}/"


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def classify_path(target: Path, path: Path, name: str = SERVICE_DIR_NAME) -> PathKind:
    """Разделить путь на продуктовый, служебный и лежащий вне `<target>`."""
    target = target.resolve()
    resolved = (path if path.is_absolute() else target / path).resolve()
    if not _inside(resolved, target):
        return PathKind.OUTSIDE
    if _inside(resolved, service_dir(target, name)):
        return PathKind.SERVICE
    return PathKind.PRODUCT


def forbidden_paths(charter_root: Path) -> tuple[Path, ...]:
    """Перечень CON-07, разрешённый относительно корня устава."""
    root = charter_root.resolve()
    return tuple((root / rel).resolve() for rel in FORBIDDEN_RELATIVE)


def is_forbidden(path: Path, charter_root: Path) -> bool:
    """Лежит ли путь в перечне CON-07 (сам путь или внутри него)."""
    resolved = path.resolve()
    return any(_inside(resolved, bad) for bad in forbidden_paths(charter_root))
