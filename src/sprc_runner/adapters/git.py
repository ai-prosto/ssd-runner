"""Git-адаптер: сводка диффа, индексирование продуктовых путей, коммит, откат.

Реализация `GitPort` через подпроцесс `git`. Индексируются и откатываются
только продуктовые пути внутри `<target>` вне перечня CON-07; служебный
каталог `.sprc/` не затрагивается ни индексированием, ни откатом.
"""

import subprocess
from pathlib import Path

from sprc_runner.core.guard import is_product_path
from sprc_runner.core.layout import SERVICE_DIR_NAME, is_forbidden
from sprc_runner.ports import DiffEntry, DiffSummary

MAX_SECRET_SCAN_BYTES = 1_000_000


class GitError(RuntimeError):
    """Команда git завершилась ошибкой."""


class GitRepo:
    """Реализация `GitPort`: репозиторий задан целевым каталогом и корнем устава."""

    def __init__(
        self, target: Path, charter_root: Path, service_name: str = SERVICE_DIR_NAME
    ) -> None:
        self._target = target.resolve()
        self._charter_root = charter_root.resolve()
        self._service = service_name

    # --- низкий уровень ---------------------------------------------------

    def _git(
        self, workdir: Path, *args: str, check: bool = True
    ) -> subprocess.CompletedProcess[bytes]:
        proc = subprocess.run(
            ["git", "--literal-pathspecs", *args],
            cwd=workdir,
            capture_output=True,
            check=False,
        )
        if check and proc.returncode != 0:
            raise GitError(
                f"git {' '.join(args)}: {proc.stderr.decode(errors='replace').strip()}"
            )
        return proc

    def _root(self, workdir: Path) -> Path:
        out = self._git(workdir, "rev-parse", "--show-toplevel").stdout
        return Path(out.decode().strip()).resolve()

    def _has_head(self, workdir: Path) -> bool:
        return (
            self._git(
                workdir, "rev-parse", "--verify", "-q", "HEAD", check=False
            ).returncode
            == 0
        )

    def _product(self, path: Path) -> bool:
        return is_product_path(path, self._target, self._charter_root)

    def _in_service(self, path: Path) -> bool:
        service = self._target / self._service
        return path == service or service in path.parents

    # --- GitPort ------------------------------------------------------------

    def diff(self, workdir: Path) -> str:
        """Текст диффа отслеживаемых файлов относительно последнего коммита."""
        if not self._has_head(workdir):
            return ""
        out = self._git(workdir, "diff", "--no-renames", "HEAD").stdout
        return out.decode(errors="replace")

    def diff_summary(self, workdir: Path) -> DiffSummary:
        """Сводка диффа рабочего каталога относительно последнего коммита."""
        root = self._root(workdir)
        head = self._has_head(workdir)
        status = self._git(
            workdir, "status", "--porcelain=v1", "-z", "--untracked-files=all"
        ).stdout.split(b"\0")
        changes: dict[Path, str] = {}
        i = 0
        while i < len(status) and status[i]:
            code, rel = status[i][:2].decode(), status[i][3:].decode()
            i += 1
            if code[0] in "RC":  # переименование/копия: следом идёт прежний путь
                old = status[i].decode()
                i += 1
                changes[(root / old).resolve()] = "D"
                changes[(root / rel).resolve()] = "A"
                continue
            if "D" in code:
                kind = "D"
            elif code == "??" or "A" in code or not head:
                kind = "A"
            else:
                kind = "M"
            changes[(root / rel).resolve()] = kind

        counts: dict[Path, tuple[int, int]] = {}
        if head:
            raw = self._git(
                workdir, "diff", "--numstat", "--no-renames", "-z", "HEAD"
            ).stdout
            for row in filter(None, raw.split(b"\0")):
                added, removed, name = row.decode(errors="replace").split("\t", 2)
                if added != "-":
                    counts[(root / name).resolve()] = (int(added), int(removed))

        entries = []
        for path, kind in sorted(changes.items()):
            if self._in_service(path):
                continue
            rel = path.relative_to(root).as_posix()
            added, removed = counts.get(path, (0, 0))
            before = 0
            if kind in "MD" and head:
                blob = self._git(
                    workdir, "cat-file", "-p", f"HEAD:{rel}", check=False
                ).stdout
                before = blob.count(b"\n") + (
                    1 if blob and not blob.endswith(b"\n") else 0
                )
            text = ""
            if kind != "D":
                text = self._added_text(root, path, rel, kind, head)
                if kind == "A" and path.is_file():
                    added = text.count("\n") + (
                        1 if text and not text.endswith("\n") else 0
                    )
            elif removed == 0:
                removed = before
            entries.append(DiffEntry(str(path), kind, added, removed, before, text))
        return DiffSummary(tuple(entries))

    def _added_text(
        self, workdir: Path, path: Path, rel: str, kind: str, head: bool
    ) -> str:
        if kind == "A" or not head:
            try:
                data = path.read_bytes()[:MAX_SECRET_SCAN_BYTES]
            except OSError:
                return ""
            return data.decode(errors="replace")
        out = self._git(
            workdir, "diff", "-U0", "--no-renames", "HEAD", "--", rel
        ).stdout
        lines = out.decode(errors="replace").splitlines()
        return "\n".join(
            ln[1:] for ln in lines if ln.startswith("+") and not ln.startswith("+++")
        )

    def stage_paths(self, workdir: Path, paths: list[str]) -> None:
        """Проиндексировать только продуктовые допустимые пути; прочие отбрасываются."""
        root = self._root(workdir)
        keep = []
        for raw in paths:
            path = Path(raw)
            path = (path if path.is_absolute() else workdir / path).resolve()
            if self._product(path):
                keep.append(path.relative_to(root).as_posix())
        if keep:
            self._git(root, "add", "-A", "--", *keep)

    def commit(self, workdir: Path, message: str) -> str:
        """Один коммит из индекса; возвращает его идентификатор."""
        ident: list[str] = []
        for key, value in (
            ("user.name", "sprc-runner"),
            ("user.email", "sprc-runner@localhost"),
        ):
            if not self._git(workdir, "config", key, check=False).stdout.strip():
                ident += ["-c", f"{key}={value}"]
        self._git(
            workdir,
            *ident,
            "commit",
            "--allow-empty",
            "--no-verify",
            "-q",
            "-m",
            message,
        )
        return self._git(workdir, "rev-parse", "HEAD").stdout.decode().strip()

    def rollback(self, workdir: Path) -> None:
        """Откатить попытку по отслеживаемым продуктовым путям, `.sprc/` не трогая."""
        if not self._has_head(workdir):
            return
        root = self._root(workdir)
        raw = self._git(
            workdir, "diff", "--name-only", "--no-renames", "-z", "HEAD"
        ).stdout
        restore, drop = [], []
        for rel in filter(None, raw.decode().split("\0")):
            path = root / rel
            # перечень CON-07 откатывается тоже: правка запретного файла не должна
            # пережить неуспешную попытку и ломать следующие
            if not (self._product(path) or is_forbidden(path, self._charter_root)):
                continue
            in_head = self._git(root, "cat-file", "-e", f"HEAD:{rel}", check=False)
            (restore if in_head.returncode == 0 else drop).append(rel)
        if restore:
            self._git(
                root,
                "restore",
                "--source=HEAD",
                "--staged",
                "--worktree",
                "--",
                *restore,
            )
        if drop:
            self._git(root, "rm", "-f", "-q", "--cached", "--", *drop)
            for rel in drop:
                (root / rel).unlink(missing_ok=True)
        # файлы, созданные попыткой и не попавшие в индекс, тоже её мусор
        raw = self._git(
            workdir, "ls-files", "--others", "--exclude-standard", "-z", "--full-name"
        ).stdout
        for rel in filter(None, raw.decode().split("\0")):
            if self._product(root / rel):
                (root / rel).unlink(missing_ok=True)
