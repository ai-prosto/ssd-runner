"""Сверка артефактов подготовки с зафиксированным эталоном формата (BEH-09).

Эталон — файл в репозитории с путём и версией источника; он же сверяется
со ссылкой в конфигурации прогона, так что формат задан не устной договорённостью.
"""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from sprc_runner.config import RunConfig
from sprc_runner.core.tasks_md import TasksFormatError, validate_tasks

ARTIFACT_KINDS = ("spec", "requirements", "tasks")


class FormatError(ValueError):
    """Артефакт не соответствует эталону формата."""


@dataclass(frozen=True)
class FormatReference:
    """Эталон: ссылка на источник и правила структуры по видам артефактов."""

    path: str
    version: str
    rules: dict[str, dict]


def load_reference(file: Path) -> FormatReference:
    """Прочитать эталон формата из `file`."""
    data = tomllib.loads(file.read_text(encoding="utf-8"))
    return FormatReference(
        str(data["path"]), str(data["version"]), {k: data[k] for k in ARTIFACT_KINDS}
    )


def check_source_link(ref: FormatReference, config: RunConfig) -> None:
    """Убедиться, что ссылка в конфигурации указывает на этот эталон."""
    if (config.format_skill_path, config.format_skill_version) != (
        ref.path,
        ref.version,
    ):
        raise FormatError(
            f"источник формата в конфигурации ({config.format_skill_path}"
            f"@{config.format_skill_version}) не совпадает с эталоном "
            f"({ref.path}@{ref.version})"
        )


def check_artifact(kind: str, text: str, ref: FormatReference) -> None:
    """Сверить артефакт вида `kind` с эталоном или поднять `FormatError`."""
    if kind not in ARTIFACT_KINDS:
        raise FormatError(f"неизвестный вид артефакта: {kind}")
    rules = ref.rules[kind]
    if kind == "tasks":
        try:
            count = len(validate_tasks(text))
        except TasksFormatError as exc:
            raise FormatError(f"tasks: {exc}") from exc
        if count < rules["min_tasks"]:
            raise FormatError(
                f"tasks: задач {count}, нужно не меньше {rules['min_tasks']}"
            )
        return
    lines = text.splitlines()
    if rules.get("title") and not any(re.match(r"# \S", ln) for ln in lines):
        raise FormatError(f"{kind}: нет заголовка первого уровня")
    sections = sum(1 for ln in lines if re.match(r"## \S", ln))
    if sections < rules["min_sections"]:
        raise FormatError(f"{kind}: разделов {sections}, нужно {rules['min_sections']}")
    pattern = rules.get("id_pattern")
    if pattern and not re.search(pattern, text):
        raise FormatError(f"{kind}: нет идентификатора вида {pattern}")
