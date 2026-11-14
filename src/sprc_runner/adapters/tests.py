"""Раннер тестов: запускает команду из конфигурации и сохраняет вывод в файл.

Ядру возвращаются только код возврата и ссылка на сохранённый вывод; текст
вывода адаптер не разбирает и ядру не передаёт (BEH-20).
"""

import shlex
import subprocess
from pathlib import Path

from sprc_runner.ports import TestRunResult

RETURNCODE_NOT_STARTED = 127
"""Команда не запустилась: исход «не зелёные», как и у ненулевого кода."""


class SubprocessTestRunner:
    """Реализация `TestRunnerPort` через подпроцесс."""

    __test__ = False

    def __init__(self, output_dir: Path, timeout_seconds: float | None = None) -> None:
        self._output_dir = output_dir
        self._timeout = timeout_seconds
        self._count = 0

    def run(self, workdir: Path, command: str) -> TestRunResult:
        self._count += 1
        self._output_dir.mkdir(parents=True, exist_ok=True)
        ref = self._output_dir / f"tests-{self._count:04d}.log"
        try:
            argv = shlex.split(command)
            if not argv:
                raise ValueError("пустая команда тестов")
            proc = subprocess.run(
                argv,
                cwd=workdir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=self._timeout,
                check=False,
            )
            code, output = proc.returncode, proc.stdout
        except subprocess.TimeoutExpired as exc:
            code, output = RETURNCODE_NOT_STARTED, (exc.stdout or b"")
        except (OSError, ValueError) as exc:
            code, output = RETURNCODE_NOT_STARTED, str(exc).encode()
        ref.write_bytes(output)
        return TestRunResult(command, code, str(ref))
