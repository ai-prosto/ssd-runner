"""BEH-29: `state.json` обновляется целиком атомарно на каждой засечке."""

import io
import json
from pathlib import Path

from sprc_runner.cli import LoopPorts, main
from sprc_runner.core import state as st
from sprc_runner.core.checkpoint import RunCheckpoint
from sprc_runner.doubles import (
    EchoAgent,
    FakeReviewer,
    FakeSpecChecker,
    FakeTestRunner,
)
from sprc_runner.ports import Outcome, StageRequest, StageResult

ROOT = Path(__file__).resolve().parents[2]

TASKS = (
    "### TASK-001: Первая\nP2 | ⬜ TODO   Est: 1d\n\n**Checklist:**\n- [ ] a\n\n"
    "### TASK-002: Вторая\nP2 | ⬜ TODO   Est: 1d\n\n**Checklist:**\n- [ ] b\n"
)


class SnoopAgent(EchoAgent):
    """Снимает `state.json` в момент каждой инвокации: что видит наблюдатель."""

    def __init__(self, out: io.StringIO) -> None:
        super().__init__()
        self.out = out
        self.snapshots: list[tuple[str, dict]] = []

    def _state(self) -> dict | None:
        first = self.out.getvalue().splitlines()[0]
        path = Path(first.removeprefix("Каталог прогона: ")) / "state.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    async def invoke(self, request: StageRequest) -> StageResult:
        snap = self._state()
        assert snap is not None
        self.snapshots.append((request.stage, snap))
        result = await super().invoke(request)
        if request.stage == "prepare_tasks":
            return StageResult(Outcome.DONE, TASKS, None, "end_turn")
        return result


def test_state_reflects_every_checkpoint(tmp_path: Path, config_file: Path) -> None:
    out = io.StringIO()
    agent = SnoopAgent(out)
    loop = LoopPorts(FakeReviewer(), FakeTestRunner([1, 0, 0]), FakeSpecChecker())
    code = main(
        ["run", "Описание.", "--config", str(config_file)],
        cwd=tmp_path,
        agent=agent,
        out=out,
        loop=loop,
    )
    assert code == 0
    snaps = agent.snapshots
    stages = [s for s, _ in snaps]
    # при старте прогона документ уже есть: этапы ждут, задач нет
    first = snaps[0][1]
    assert first["schema_version"] == st.STATE_SCHEMA_VERSION
    assert [x["status"] for x in first["stages"]] == ["pending"] * 3
    assert first["tasks"] == [] and first["finished"] is False
    # завершение этапа отражено до следующего этапа, с применённой моделью
    second = snaps[stages.index("prepare_requirements")][1]
    assert second["stages"][0]["status"] == "done"
    assert second["stages"][0]["model"] == first["config"]["model_prepare"]
    # старт попытки: задача в работе, курсор на попытке
    impl = [s for st_, s in snaps if st_ == "implement"]
    assert impl[0]["tasks"][0]["status"] == "in_progress"
    assert impl[0]["cursor"] == {"task_id": "TASK-001", "attempt": 1}
    assert impl[0]["tasks"][0]["attempts"] == 0
    # после неуспешной первой попытки — число попыток и её описание
    assert impl[1]["tasks"][0]["attempts"] == 1
    assert impl[1]["tasks"][0]["last_attempt"] == {
        "number": 1,
        "success": False,
        "error_code": "tests_failed",
    }
    assert impl[1]["cursor"] == {"task_id": "TASK-001", "attempt": 2}
    # после завершения первой задачи вторая берётся с первой выполненной
    assert impl[2]["tasks"][0]["status"] == "done"
    assert impl[2]["tasks"][0]["attempts"] == 2
    assert impl[2]["tasks"][1]["status"] == "in_progress"
    assert impl[2]["cursor"] == {"task_id": "TASK-002", "attempt": 1}
    # итог: прогон завершён с кодом причины, курсора нет
    run_dir = Path(out.getvalue().splitlines()[0].removeprefix("Каталог прогона: "))
    final = st.decode_state((run_dir / "state.json").read_text(encoding="utf-8"))
    assert final.finished and final.reason == "all_succeeded" and final.cursor is None
    assert [t.status for t in final.tasks] == [st.TaskStatus.DONE] * 2
    assert not list(run_dir.glob(".*.tmp"))


class RecordingFiles:
    """Порт файлов, помнящий способ записи каждого файла."""

    def __init__(self) -> None:
        self.atomic: list[Path] = []
        self.other = 0

    def read_text(self, path: Path) -> str:  # pragma: no cover
        return path.read_text(encoding="utf-8")

    def write_atomic(self, path: Path, text: str) -> None:
        self.atomic.append(path)
        json.loads(text) if path.name == "state.json" else None

    def append_line(self, path: Path, line: str) -> None:  # pragma: no cover
        self.other += 1


def test_every_checkpoint_is_one_atomic_full_write(tmp_path: Path) -> None:
    files = RecordingFiles()
    state = st.new_state("r", "h", {}, ["prepare_spec"])
    cp = RunCheckpoint(tmp_path, state, files, tmp_path / "tasks.md")
    cp.save()
    cp.stage_finished("prepare_spec", "m", True)
    cp.tasks_registered(["T-1"])
    cp.task_taken("T-1")
    cp.attempt_started("T-1", 1)
    cp.attempt_finished("T-1", 1, True, None, "abc", counted=True)
    cp.task_finished("T-1", True, "text")
    cp.paused("interrupted")
    cp.finished("all_succeeded")
    state_writes = [p for p in files.atomic if p.name == "state.json"]
    assert len(state_writes) == 9  # по одной на каждую засечку
    assert tmp_path / "tasks.md" in files.atomic  # отметка пишется тем же способом
    assert files.other == 0


def test_schema_document_matches_reader() -> None:
    doc = (ROOT / "schemas" / "state-schema.md").read_text(encoding="utf-8")
    assert f"Версия схемы: {st.STATE_SCHEMA_VERSION}" in doc
    for status in st.TaskStatus:
        assert status.value in doc
    for key in json.loads(st.encode_state(st.new_state("r", "h", {}, []))):
        assert f"`{key}`" in doc or f"`{key}[]`" in doc


def test_documents_with_unknown_major_version_rejected() -> None:
    data = json.loads(st.encode_state(st.new_state("r", "h", {}, [])))
    data["schema_version"] = "9.0"
    try:
        st.decode_state(json.dumps(data))
    except st.StateFormatError:
        return
    raise AssertionError("чужой мажор должен отвергаться")
