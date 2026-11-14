"""DEL-19: адаптер тестов возвращает код возврата и ссылку на сохранённый вывод."""

import sys
from pathlib import Path

from sprc_runner.adapters.tests import RETURNCODE_NOT_STARTED, SubprocessTestRunner


def test_returns_code_and_output_ref(tmp_path):
    runner = SubprocessTestRunner(tmp_path / "out")
    cmd = f"{sys.executable} -c \"print('hello'); raise SystemExit(3)\""
    result = runner.run(tmp_path, cmd)
    assert result.returncode == 3
    assert result.command == cmd
    assert "hello" in Path(result.output_ref).read_text()


def test_zero_code(tmp_path):
    result = SubprocessTestRunner(tmp_path).run(tmp_path, f"{sys.executable} -c pass")
    assert result.returncode == 0


def test_missing_and_empty_command_are_not_green(tmp_path):
    runner = SubprocessTestRunner(tmp_path)
    assert (
        runner.run(tmp_path, "no-such-binary-xyz").returncode == RETURNCODE_NOT_STARTED
    )
    assert runner.run(tmp_path, "  ").returncode == RETURNCODE_NOT_STARTED


def test_timeout_is_not_green(tmp_path):
    runner = SubprocessTestRunner(tmp_path, timeout_seconds=0.2)
    cmd = f'{sys.executable} -c "import time; time.sleep(5)"'
    assert runner.run(tmp_path, cmd).returncode == RETURNCODE_NOT_STARTED
