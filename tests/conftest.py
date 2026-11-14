import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def config_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("cfg") / "sprc-runner.toml"
    shutil.copy(ROOT / "sprc-runner.toml", path)
    return path


@pytest.fixture
def set_models(config_file: Path) -> Callable[..., None]:
    """Переписать ключи моделей в копии конфигурации."""

    def apply(**models: str) -> None:
        lines = []
        for line in config_file.read_text(encoding="utf-8").splitlines():
            key = line.split("=")[0].strip()
            if key in models:
                line = f'{key} = "{models[key]}"'
            lines.append(line)
        config_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return apply
