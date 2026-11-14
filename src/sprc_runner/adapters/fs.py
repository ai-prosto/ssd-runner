"""Файловая система: атомарная перезапись и дописывание журнала со сбросом на диск."""

import os
from pathlib import Path


class LocalFiles:
    """Реализация `FilePort` на локальной файловой системе."""

    def read_text(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def write_atomic(self, path: Path, text: str) -> None:
        """Записать файл целиком: временный файл рядом, fsync, переименование."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)

    def append_line(self, path: Path, line: str) -> None:
        """Дописать строку (с переводом строки) и сбросить её на диск."""
        path.parent.mkdir(parents=True, exist_ok=True)
        data = (line.rstrip("\n") + "\n").encode("utf-8")
        with open(path, "ab") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
