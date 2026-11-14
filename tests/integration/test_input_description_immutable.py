"""BEH-12: входное описание фиксируется в каталоге прогона неизменным."""

from pathlib import Path

from sprc_runner.cli import main
from sprc_runner.doubles import EchoAgent

RAW = "Описание\r\nс разными\tсимволами  \n\nи без финального перевода".encode()


def _only_run_dir(cwd: Path) -> Path:
    (run_dir,) = (cwd / ".sdd-runs" / ".sprc").iterdir()
    return run_dir


def test_text_input_copied_byte_for_byte(tmp_path: Path, config_file: Path) -> None:
    text = RAW.decode()
    main(["run", text, "--config", str(config_file)], cwd=tmp_path, agent=EchoAgent())
    assert (_only_run_dir(tmp_path) / "input.md").read_bytes() == RAW


def test_file_input_copied_byte_for_byte(tmp_path: Path, config_file: Path) -> None:
    src = tmp_path / "in.bin"
    raw = RAW + b"\xff\x00"
    src.write_bytes(raw)
    main(
        ["run", "--file", str(src), "--config", str(config_file)],
        cwd=tmp_path,
        agent=EchoAgent(),
    )
    assert (_only_run_dir(tmp_path) / "input.md").read_bytes() == raw
