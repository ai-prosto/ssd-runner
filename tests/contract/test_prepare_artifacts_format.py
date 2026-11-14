"""BEH-09: артефакты подготовки сверяются с зафиксированным эталоном формата."""

import asyncio
from pathlib import Path

import pytest

from sprc_runner.config import load_config
from sprc_runner.core.formats import (
    FormatError,
    check_artifact,
    check_source_link,
    load_reference,
)
from sprc_runner.core.prepare import init_run, run_preparation
from sprc_runner.ports import Outcome, StageRequest, StageResult

ROOT = Path(__file__).resolve().parents[2]
CONFIG = load_config(ROOT / "sprc-runner.toml")
REF = load_reference(ROOT / CONFIG.format_skill_path)

SPEC = "# Спека\n\n## Цель\nТекст.\n"
REQ = "# Требования\n\n## Функциональные\n- FR-01 делать.\n"
TASKS = "### TASK-001: Первая\nP2 | ⬜ TODO   Est: 1d\n\n**Checklist:**\n- [ ] a\n"


class FormattedAgent:
    def __init__(self) -> None:
        self.calls: list[StageRequest] = []

    async def invoke(self, request: StageRequest) -> StageResult:
        self.calls.append(request)
        text = {"prepare_spec": SPEC, "prepare_requirements": REQ}.get(
            request.stage, TASKS
        )
        return StageResult(Outcome.DONE, text, None, "end_turn")

    async def close(self) -> None:
        pass


def test_source_link_in_config_matches_reference() -> None:
    check_source_link(REF, CONFIG)
    assert (REF.path, REF.version) == (
        CONFIG.format_skill_path,
        CONFIG.format_skill_version,
    )


def test_mismatching_source_link_rejected() -> None:
    from dataclasses import replace

    with pytest.raises(FormatError, match="не совпадает"):
        check_source_link(REF, replace(CONFIG, format_skill_version="999"))


def test_produced_artifacts_conform_to_reference(tmp_path: Path) -> None:
    run_dir = init_run(tmp_path, "r1", "Описание".encode())
    agent = FormattedAgent()
    paths = asyncio.run(run_preparation(agent, CONFIG, run_dir))
    for kind, path in (
        ("spec", paths.spec),
        ("requirements", paths.requirements),
        ("tasks", paths.tasks),
    ):
        check_artifact(kind, path.read_text(encoding="utf-8"), REF)


def test_source_substituted_into_inputs_without_changing_shape(tmp_path: Path) -> None:
    run_dir = init_run(tmp_path, "r1", "Описание".encode())
    agent = FormattedAgent()
    asyncio.run(run_preparation(agent, CONFIG, run_dir))
    assert [len(c.inputs) for c in agent.calls] == [2, 3, 3]
    for call in agent.calls:
        assert CONFIG.format_skill_path in call.inputs[0]
        assert CONFIG.format_skill_version in call.inputs[0]


@pytest.mark.parametrize(
    ("kind", "text", "message"),
    [
        ("spec", "без заголовка\n## Раздел\n", "заголовка"),
        ("spec", "# Заголовок\nбез разделов\n", "разделов"),
        ("requirements", "# Т\n## Р\nбез идентификаторов\n", "идентификатора"),
        ("tasks", "просто текст\n", "tasks"),
        ("other", "x", "неизвестный"),
    ],
)
def test_nonconforming_artifact_rejected(kind: str, text: str, message: str) -> None:
    with pytest.raises(FormatError, match=message):
        check_artifact(kind, text, REF)
