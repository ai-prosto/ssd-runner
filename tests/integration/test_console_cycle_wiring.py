"""BEH-43: консольные `run` и `resume` сами собирают порты; подменён лишь агент."""

import io
from pathlib import Path

import pytest

from sprc_runner import console
from sprc_runner.adapters.reviewer import SdkReviewer, parse_verdict
from sprc_runner.core.state import decode_state
from sprc_runner.doubles import EchoAgent
from sprc_runner.ports import Outcome, StageRequest, StageResult
from tests.git_helpers import git, make_repo

TASKS = (
    "### TASK-001: Первая\nP2 | ⬜ TODO   Est: 1d\n\n**Checklist:**\n- [ ] a\n\n"
    "### TASK-002: Вторая\nP2 | ⬜ TODO   Est: 1d\n\n**Checklist:**\n- [ ] b\n"
)


class ProtocolAgent(EchoAgent):
    """Подставной агент протокола: единственное, что подменено."""

    def __init__(self, verdict: str = "APPROVED\nok", limit_on: int | None = None):
        super().__init__()
        self.verdict = verdict
        self.limit_on = limit_on
        self.workdir: Path | None = None
        self.implemented = 0

    async def invoke(self, request: StageRequest) -> StageResult:
        self.calls.append(request)
        if request.stage == "prepare_tasks":
            return StageResult(Outcome.DONE, TASKS, None, "end_turn")
        if request.stage == "implement":
            self.implemented += 1
            if self.limit_on == self.implemented:
                return StageResult(Outcome.SUBSCRIPTION_LIMIT, "", None, "limit")
            (request.workdir / f"mod{self.implemented}.py").write_text("x = 1\n")
        if request.stage == "review":
            return StageResult(Outcome.DONE, self.verdict, None, "end_turn")
        # этапы подготовки: заголовок обязателен — текст без него считается отказом
        return StageResult(Outcome.DONE, "# ok", None, "end_turn")


@pytest.fixture
def repo_config(tmp_path: Path, config_file: Path) -> tuple[Path, Path]:
    lines = [
        'test_command = "true"' if ln.startswith("test_command") else ln
        for ln in config_file.read_text(encoding="utf-8").splitlines()
    ]
    config_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return make_repo(tmp_path / "repo"), config_file


def run_console(argv: list[str], repo: Path, agent: ProtocolAgent):
    out, err = io.StringIO(), io.StringIO()
    code = console(argv, cwd=repo, agent=agent, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def test_run_executes_cycle_without_programmatic_ports(repo_config, set_models) -> None:
    repo, cfg = repo_config
    set_models(model_review="reviewer-model")
    agent = ProtocolAgent()
    code, out, _ = run_console(["run", "Описание.", "--config", str(cfg)], repo, agent)
    assert code == 0
    stages = [c.stage for c in agent.calls]
    assert stages.count("implement") == 2
    assert stages.count("review") == 2
    review = next(c for c in agent.calls if c.stage == "review")
    assert review.model == "reviewer-model"
    log = git(repo, "log", "--format=%s")
    assert "TASK-001" in log and "TASK-002" in log
    assert "prepared" not in out


def test_rejected_review_blocks_commit(repo_config) -> None:
    repo, cfg = repo_config
    agent = ProtocolAgent(verdict="REJECTED\nплохо")
    code, _, _ = run_console(["run", "Описание.", "--config", str(cfg)], repo, agent)
    assert code != 0
    assert "TASK-001" not in git(repo, "log", "--format=%s")


def test_resume_continues_cycle_without_ports(repo_config) -> None:
    repo, cfg = repo_config
    first = ProtocolAgent(limit_on=2)
    code, out, _ = run_console(["run", "Описание.", "--config", str(cfg)], repo, first)
    assert code != 0
    run_dir = Path(out.splitlines()[0].removeprefix("Каталог прогона: "))
    assert decode_state((run_dir / "state.json").read_text()).tasks
    second = ProtocolAgent()
    argv = ["resume", str(run_dir), "--config", str(cfg)]
    code, _, _ = run_console(argv, repo, second)
    assert code == 0
    assert [c.stage for c in second.calls].count("implement") >= 1
    assert "TASK-002" in git(repo, "log", "--format=%s")


@pytest.mark.parametrize(
    ("text", "approved"),
    [
        ("APPROVED\nok", True),
        ("**REJECTED**\nбаг", False),
        ("", False),
        ("looks fine", False),
        # серия 4: преамбула перед вердиктом — честный APPROVED не теряется
        (
            "Я ревьюирую TASK-003. Тесты не гонял.\n\nAPPROVED\n\n- всё по чек-листу",
            True,
        ),
        ("Проверил.\n**APPROVED**", True),
        # противоречивые вердикты — fail-closed
        ("APPROVED\nно вообще REJECTED", True),
        ("APPROVED\nREJECTED", False),
    ],
)
def test_parse_verdict(text: str, approved: bool) -> None:
    assert parse_verdict(text)[0] is approved


@pytest.mark.anyio
async def test_reviewer_fails_closed_on_agent_failure() -> None:
    class Failing(EchoAgent):
        async def invoke(self, request: StageRequest) -> StageResult:
            return StageResult(Outcome.TRANSPORT_ERROR, "", None, "boom")

    request = StageRequest("review", "m", ("t",), Path("."), ("Read",), 1.0)
    verdict = await SdkReviewer(Failing()).review(request)
    assert not verdict.approved
    assert verdict.reviewer_id == "model:m"
