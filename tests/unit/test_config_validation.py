import io
from pathlib import Path

import pytest

from sprc_runner.config import (
    EXIT_CONFIG_REFUSED,
    ConfigError,
    load_config,
    load_config_or_report,
    parse_config,
)

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
"""


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "c.toml"
    p.write_text(text)
    return p


def test_missing_file_names_path(tmp_path: Path) -> None:
    p = tmp_path / "absent.toml"
    err = io.StringIO()
    result = load_config_or_report(p, err)
    assert result == EXIT_CONFIG_REFUSED
    assert str(p) in err.getvalue()


def test_unknown_key_named(tmp_path: Path) -> None:
    p = _write(tmp_path, VALID + 'bogus_key = "x"\n')
    err = io.StringIO()
    assert load_config_or_report(p, err) == EXIT_CONFIG_REFUSED
    assert "bogus_key" in err.getvalue()


def test_empty_model_named(tmp_path: Path) -> None:
    p = _write(tmp_path, VALID.replace('model_review = "m4"', 'model_review = ""'))
    err = io.StringIO()
    assert load_config_or_report(p, err) == EXIT_CONFIG_REFUSED
    assert "model_review" in err.getvalue()


def test_missing_model_named(tmp_path: Path) -> None:
    p = _write(tmp_path, VALID.replace('model_tasks = "m2"\n', ""))
    with pytest.raises(ConfigError) as ei:
        load_config(p)
    assert ei.value.keys == ["model_tasks"]


def test_exit_code_distinct_from_success_and_crash() -> None:
    assert EXIT_CONFIG_REFUSED not in (0, 1, 2, 3)


def test_broken_toml(tmp_path: Path) -> None:
    err = io.StringIO()
    p = _write(tmp_path, "model_prepare = ")
    assert load_config_or_report(p, err) == EXIT_CONFIG_REFUSED


def test_invalid_utf8(tmp_path: Path) -> None:
    p = tmp_path / "c.toml"
    p.write_bytes(b"\xff\xfe")
    with pytest.raises(ConfigError):
        load_config(p)


@pytest.mark.parametrize("value", ["0", "true", '"3"'])
def test_bad_max_attempts(value: str) -> None:
    import tomllib

    data = tomllib.loads(VALID.replace("max_attempts = 5", f"max_attempts = {value}"))
    with pytest.raises(ConfigError) as ei:
        parse_config(data)
    assert ei.value.keys == ["max_attempts"]


def test_success_returns_config(tmp_path: Path) -> None:
    result = load_config_or_report(_write(tmp_path, VALID), io.StringIO())
    assert not isinstance(result, int)
