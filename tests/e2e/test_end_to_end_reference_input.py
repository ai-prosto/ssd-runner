"""BEH-11: сквозной прогон эталонного описания даёт код и тесты в целевом каталоге.

Эталонное входное описание зафиксировано в репозитории как тестовые данные
(`tests/data/reference_input.md`, допущение A-02 требований). Прогон идёт
целиком — подготовка, цикл задач, отметки — на подставных агенте и раннере
тестов; способ подачи описания (текстом или путём к файлу) на состав
и структуру результата не влияет.
"""

from pathlib import Path

from sprc_runner.cli import LoopPorts, main
from sprc_runner.doubles import (
    EchoAgent,
    FakeReviewer,
    FakeSpecChecker,
    FakeTestRunner,
)
from sprc_runner.ports import Outcome, StageRequest, StageResult

REFERENCE = Path(__file__).resolve().parents[1] / "data" / "reference_input.md"

TASKS = (
    "### TASK-001: Ядро калькулятора\nP2 | ⬜ TODO   Est: 1d\n\n"
    "**Checklist:**\n- [ ] операции\n\n"
    "### TASK-002: Командная строка\nP2 | ⬜ TODO   Est: 1d\n\n"
    "**Checklist:**\n- [ ] вызов\n"
)
PRODUCT_FILES = (
    "app/task_001.py",
    "tests/test_task_001.py",
    "app/task_002.py",
    "tests/test_task_002.py",
)


class CodingAgent(EchoAgent):
    """Агент: на `implement` кладёт код и тест задачи в целевой каталог."""

    async def invoke(self, request: StageRequest) -> StageResult:
        if request.stage == "prepare_tasks":
            self.calls.append(request)
            return StageResult(Outcome.DONE, TASKS, None, "end_turn")
        if request.stage == "implement":
            self.calls.append(request)
            task = request.inputs[0].split(":")[0].strip().lower().replace("-", "_")
            files = {
                request.workdir / "app" / f"{task}.py": f'"""{task}."""\nVALUE = 1\n',
                request.workdir
                / "tests"
                / f"test_{task}.py": "def test_value() -> None:\n    assert True\n",
            }
            for path, text in files.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            return StageResult(Outcome.DONE, "код и тест написаны", None, "end_turn")
        return await super().invoke(request)


def _run(tmp_path: Path, config_file: Path, source: list[str]) -> Path:
    """Полный прогон без вмешательства после старта; вернуть каталог прогона."""
    code = main(
        ["run", *source, "--config", str(config_file)],
        cwd=tmp_path,
        agent=CodingAgent(),
        loop=LoopPorts(FakeReviewer(), FakeTestRunner(), FakeSpecChecker()),
    )
    assert code == 0
    (run_dir,) = (tmp_path / ".sdd-runs" / ".sprc").iterdir()
    return run_dir


def test_reference_description_is_repository_test_data() -> None:
    text = REFERENCE.read_text(encoding="utf-8")
    assert text.strip(), "эталонное описание пусто"
    for section in ("Функции", "Ограничения", "Критерии успеха", "Масштабируемость"):
        assert section in text, f"в эталонном описании нет раздела «{section}»"


def test_text_input_yields_code_and_tests_in_target(
    tmp_path: Path, config_file: Path
) -> None:
    run_dir = _run(tmp_path, config_file, [REFERENCE.read_text(encoding="utf-8")])
    for name in ("spec.md", "requirements.md", "tasks.md"):
        assert (run_dir / name).read_text(encoding="utf-8").strip(), name
    tasks = (run_dir / "tasks.md").read_text(encoding="utf-8")
    assert tasks.count("✅ DONE") == 2  # обе задачи списка выполнены и отмечены
    target = tmp_path / ".sdd-runs"
    for rel in PRODUCT_FILES:
        assert (target / rel).is_file(), f"в целевом каталоге нет файла {rel}"


def test_file_input_gives_same_artifacts_and_product(
    tmp_path: Path, config_file: Path
) -> None:
    by_text = _run(tmp_path / "a", config_file, [REFERENCE.read_text(encoding="utf-8")])
    by_file = _run(tmp_path / "b", config_file, ["--file", str(REFERENCE)])
    for name in ("spec.md", "requirements.md", "tasks.md"):
        assert (by_text / name).read_bytes() == (by_file / name).read_bytes(), name
    for rel in PRODUCT_FILES:
        ours = tmp_path / "a" / ".sdd-runs" / rel
        theirs = tmp_path / "b" / ".sdd-runs" / rel
        assert ours.read_bytes() == theirs.read_bytes(), rel
