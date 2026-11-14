from pathlib import Path

import pytest

from sprc_runner.config import ALL_KEYS, load_config

VALID = """
model_prepare = "m1"
model_tasks = "m2"
model_implement = "m3"
model_review = "m4"
max_attempts = 5
target_dir = "out"
run_dir = ".sprc"
log_dir = "lg"
log_level = "DEBUG"
test_command = "make test"
format_skill_path = "fmt/skill"
format_skill_version = "7"
"""


def test_all_settings_come_from_file(tmp_path: Path) -> None:
    p = tmp_path / "c.toml"
    p.write_text(VALID)
    cfg = load_config(p)
    assert (cfg.model_prepare, cfg.model_tasks) == ("m1", "m2")
    assert (cfg.model_implement, cfg.model_review) == ("m3", "m4")
    assert cfg.max_attempts == 5
    assert cfg.target_dir == "out"
    assert cfg.log_dir == "lg"
    assert cfg.log_level == "DEBUG"
    assert cfg.test_command == "make test"


def test_environment_does_not_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / "c.toml"
    p.write_text(VALID)
    for key in ALL_KEYS:
        monkeypatch.setenv(key.upper(), "env")
        monkeypatch.setenv(f"SPRC_{key.upper()}", "env")
    cfg = load_config(p)
    assert "env" not in vars(cfg).values()


def test_file_change_changes_value(tmp_path: Path) -> None:
    p = tmp_path / "c.toml"
    p.write_text(VALID.replace('"m3"', '"other"'))
    assert load_config(p).model_implement == "other"


def test_shipped_config_is_valid() -> None:
    root = Path(__file__).resolve().parents[2]
    assert load_config(root / "sprc-runner.toml").max_attempts >= 1
