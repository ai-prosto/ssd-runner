"""Конфигурация прогона: единственный файл настроек и валидация до старта.

Все настройки прогона читаются только из одного TOML-файла (BEH-02): ни переменные
окружения, ни второй файл, ни значения по умолчанию в коде их не подменяют.
Любая проблема файла (нет файла, неизвестный ключ, пустая модель) отказывает
на старте с именем проблемного ключа и кодом `EXIT_CONFIG_REFUSED` (BEH-03).
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

EXIT_CONFIG_REFUSED = 4
"""Код возврата отказа на старте по конфигурации (не 0, не 1, не коды прогона)."""

MODEL_KEYS = ("model_prepare", "model_tasks", "model_implement", "model_review")
STR_KEYS = ("target_dir", "run_dir", "log_dir", "log_level", "test_command")
INT_KEYS = ("max_attempts",)
ALL_KEYS = (*MODEL_KEYS, *INT_KEYS, *STR_KEYS)


@dataclass(frozen=True)
class RunConfig:
    """Значение конфигурации прогона, собранное из единственного файла."""

    model_prepare: str
    model_tasks: str
    model_implement: str
    model_review: str
    max_attempts: int
    target_dir: str
    run_dir: str
    log_dir: str
    log_level: str
    test_command: str


class ConfigError(Exception):
    """Конфигурация непригодна; `keys` — имена проблемных ключей."""

    def __init__(self, keys: list[str], reason: str) -> None:
        self.keys = keys
        self.reason = reason
        super().__init__(f"{', '.join(keys)}: {reason}")


def parse_config(data: dict[str, Any]) -> RunConfig:
    """Проверить словарь настроек и собрать `RunConfig` либо поднять `ConfigError`."""
    for key in data:
        if key not in ALL_KEYS:
            raise ConfigError([str(key)], "неизвестный ключ")
    for key in ALL_KEYS:
        if key not in data:
            raise ConfigError([key], "ключ не задан")
    for key in (*MODEL_KEYS, *STR_KEYS):
        value = data[key]
        if not isinstance(value, str) or not value.strip():
            raise ConfigError([key], "значение не заполнено")
    attempts = data["max_attempts"]
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
        raise ConfigError(["max_attempts"], "ожидается целое число не меньше 1")
    return RunConfig(**{key: data[key] for key in ALL_KEYS})


def load_config(path: Path) -> RunConfig:
    """Прочитать единственный файл настроек `path` и провалидировать его."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ConfigError(
            [str(path)], f"файл конфигурации недоступен: {exc.strerror}"
        ) from exc
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError([str(path)], f"файл конфигурации не разобран: {exc}") from exc
    return parse_config(data)


def load_config_or_report(path: Path, err: TextIO) -> RunConfig | int:
    """Загрузить конфигурацию; при отказе напечатать ключ в `err` и вернуть код."""
    try:
        return load_config(path)
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=err)
        return EXIT_CONFIG_REFUSED
