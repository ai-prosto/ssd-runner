"""BEH-40 (DEL-54): тесты проекта проходят на Linux с uv так же, как на macOS.

На macOS живой прогон на Linux невозможен, поэтому проверяются причины, по которым
набор мог бы разойтись между системами: регистр имён файлов (в Linux файловая
система чувствительна к регистру), зависимости только под одну платформу, вызовы
macOS-инструментов, башизмы в `#!/bin/sh`-скриптах, привязка к абсолютным путям.
На самой Linux-машине дополнительно выполняется живой прогон набора через uv.
"""

import os
import re
import shutil
import subprocess
import sys
import tomllib
from collections import defaultdict
from pathlib import Path

import pytest

from tests.git_helpers import CON07_FILES

ROOT = Path(__file__).resolve().parents[2]
SELF = Path(__file__).resolve()
NESTED = "SPRC_LINUX_COMPAT_NESTED"

pytestmark = pytest.mark.integration


def _tracked() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return [p for p in out.stdout.split("\0") if p and (ROOT / p).exists()]


def _python_sources() -> list[Path]:
    return [
        p
        for p in (ROOT / d for d in ("src", "tests"))
        for p in sorted(p.rglob("*.py"))
        if p.resolve() != SELF
    ]


def test_no_case_insensitive_path_collisions() -> None:
    groups: dict[str, list[str]] = defaultdict(list)
    for path in _tracked():
        groups[path.lower()].append(path)
    clashes = [v for v in groups.values() if len(v) > 1]
    assert not clashes, f"пути различаются только регистром: {clashes}"


def test_con07_files_exist_with_exact_case() -> None:
    tracked = set(_tracked())
    for rel in CON07_FILES:
        assert rel in tracked, f"{rel}: нет в git с точным регистром имени"


def test_shell_scripts_run_by_tests_are_executable_in_git() -> None:
    out = subprocess.run(
        ["git", "ls-files", "-s", "scripts/review/local.sh"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.startswith("100755"), "local.sh должен быть исполняемым в git"


def test_sh_scripts_have_no_bashisms() -> None:
    dash = shutil.which("dash")
    if dash is None:
        pytest.skip("dash не установлен: проверка синтаксиса POSIX sh недоступна")
    for rel in _tracked():
        path = ROOT / rel
        if not rel.endswith(".sh"):
            continue
        first = path.read_text(errors="replace").splitlines()[:1]
        if first and first[0].strip() == "#!/bin/sh":
            done = subprocess.run(
                [dash, "-n", str(path)], capture_output=True, text=True
            )
            assert done.returncode == 0, f"{rel}: {done.stderr}"


def test_python_requirement_is_not_platform_bound() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert data["project"]["requires-python"].startswith(">=")
    tool_uv = data.get("tool", {}).get("uv", {})
    assert "environments" not in tool_uv, "uv.environments сузит набор платформ"


def test_lock_has_no_darwin_only_packages() -> None:
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    for pkg in lock["package"]:
        markers = pkg.get("resolution-markers", [])
        assert not any("darwin" in m and "linux" not in m for m in markers), (
            f"{pkg['name']}: разрешение только под darwin"
        )
        for wheel in pkg.get("wheels", []):
            assert "url" in wheel or "path" in wheel
    linux_wheels = [
        w
        for pkg in lock["package"]
        if pkg["name"] == "claude-agent-sdk"
        for w in pkg.get("wheels", [])
        if "linux" in w.get("url", "") or "none-any" in w.get("url", "")
    ]
    assert linux_wheels, "claude-agent-sdk не имеет колеса под Linux в uv.lock"


def test_sources_avoid_macos_only_tools_and_absolute_paths() -> None:
    banned = re.compile(
        r"\b(osascript|pbcopy|pbpaste|launchctl|sw_vers|brew)\b"
        r"|/Users/|/private/var|/System/Library|sys\.platform\s*==\s*[\"']darwin"
    )
    for path in _python_sources():
        for number, line in enumerate(path.read_text().splitlines(), 1):
            assert not banned.search(line), f"{path.relative_to(ROOT)}:{number}"


def test_sources_do_not_rely_on_macos_bsd_flags() -> None:
    bsd = re.compile(r"\bsed\s+-i\s+(''|\"\")|\bstat\s+-f\b|\bdate\s+-j\b")
    for path in _python_sources():
        text = path.read_text()
        assert not bsd.search(text), f"{path.relative_to(ROOT)}: BSD-вариант флагов"


@pytest.mark.skipif(
    not sys.platform.startswith("linux") or shutil.which("uv") is None,
    reason="живой прогон — только на Linux с установленным uv",
)
@pytest.mark.skipif(os.environ.get(NESTED) == "1", reason="вложенный прогон")
def test_suite_passes_on_linux_with_uv() -> None:
    env = {**os.environ, NESTED: "1"}
    done = subprocess.run(
        [
            "uv",
            "run",
            "--frozen",
            "pytest",
            "-q",
            "-x",
            "--deselect",
            f"{SELF.relative_to(ROOT)}",
            "-m",
            "not integration",
        ],  # fmt: skip
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-1000:]
