"""BEH-36: SDK запинен, импортируется в одном адаптере, других харнессов нет."""

import ast
import asyncio
import re
import sys
import tomllib
from pathlib import Path

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    CLIConnectionError,
    CLINotFoundError,
    ProcessError,
    ResultMessage,
    TextBlock,
)

from sprc_runner import doubles
from sprc_runner.adapters.claude_sdk import ClaudeSdkAgent, classify_failure
from sprc_runner.ports import Outcome, ReviewVerdict, StageRequest, StageResult

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "sprc_runner"
SDK = "claude_agent_sdk"


def _sources() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _imports_sdk(path: Path) -> bool:
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import) and any(
            a.name.split(".")[0] == SDK for a in node.names
        ):
            return True
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.split(".")[0] == SDK
        ):
            return True
    return False


def test_sdk_imported_in_exactly_one_adapter() -> None:
    importers = [p for p in _sources() if _imports_sdk(p)]
    assert importers == [SRC / "adapters" / "claude_sdk.py"]


def test_no_other_harness_calls() -> None:
    codex = re.compile(r"\bcodex\b", re.I)
    claude_p = re.compile(r"claude[\"',\s]+-p\b|\bclaude\s+-p\b")
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        assert not codex.search(text), path
        assert not claude_p.search(text), path


def test_sdk_version_pinned_exactly() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    specs = [
        d for d in data["project"]["dependencies"] if d.startswith("claude-agent-sdk")
    ]
    assert len(specs) == 1
    assert re.fullmatch(r"claude-agent-sdk==\d+(\.\d+)*", specs[0]), specs[0]


def test_ports_expose_no_sdk_types() -> None:
    assert not _imports_sdk(SRC / "ports.py")
    assert not _imports_sdk(SRC / "doubles.py")


def test_ports_import_without_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    for name in [m for m in sys.modules if m.startswith(("sprc_runner.ports", SDK))]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, SDK, None)
    importlib.import_module("sprc_runner.ports")
    importlib.import_module("sprc_runner.doubles")


def _request(timeout: float = 5.0) -> StageRequest:
    return StageRequest("implement", "m", ("a", "b"), Path("."), ("Read",), timeout)


def _agent(tmp_path: Path, messages=(), exc: BaseException | None = None, delay=0.0):
    async def fake_query(*, prompt, options):
        assert prompt == "a\n\nb"
        assert options.model == "m"
        for m in messages:
            yield m
        if delay:
            await asyncio.sleep(delay)
        if exc:
            raise exc

    return ClaudeSdkAgent(tmp_path / "tr", query_fn=fake_query)


def _result(
    *, is_error: bool = False, subtype: str = "success", result: str = "final"
) -> ResultMessage:
    return ResultMessage(
        subtype=subtype,
        duration_ms=1,
        duration_api_ms=1,
        is_error=is_error,
        num_turns=1,
        session_id="s",
        result=result,
        usage={"x": 1},
    )


def test_invoke_done(tmp_path: Path) -> None:
    msgs = [AssistantMessage(content=[TextBlock(text="hi")], model="m"), _result()]
    res = asyncio.run(_agent(tmp_path, msgs).invoke(_request()))
    assert res.outcome is Outcome.DONE
    assert res.text == "final"
    assert res.stop_reason == "success"
    assert res.usage == {"x": 1}
    assert res.transcript_ref and Path(res.transcript_ref).read_text().count("\n") == 2


def test_invoke_error_result_limit_and_other(tmp_path: Path) -> None:
    limit = _result(is_error=True, subtype="error", result="Usage limit reached")
    assert asyncio.run(_agent(tmp_path, [limit]).invoke(_request())).outcome is (
        Outcome.SUBSCRIPTION_LIMIT
    )
    other = _result(is_error=True, subtype="error_max_turns", result="boom")
    assert asyncio.run(_agent(tmp_path, [other]).invoke(_request())).outcome is (
        Outcome.OTHER_FAILURE
    )


def test_invoke_no_result(tmp_path: Path) -> None:
    res = asyncio.run(_agent(tmp_path, []).invoke(_request()))
    assert (res.outcome, res.stop_reason) == (Outcome.OTHER_FAILURE, "no_result")


def test_invoke_timeout(tmp_path: Path) -> None:
    res = asyncio.run(_agent(tmp_path, delay=1).invoke(_request(0.01)))
    assert (res.outcome, res.stop_reason) == (Outcome.OTHER_FAILURE, "timeout")


@pytest.mark.parametrize(
    ("exc", "outcome"),
    [
        (
            ProcessError("x", exit_code=1, stderr="Claude usage limit reached"),
            Outcome.SUBSCRIPTION_LIMIT,
        ),
        (ProcessError("x", exit_code=1), Outcome.OTHER_FAILURE),
        (CLIConnectionError("down"), Outcome.TRANSPORT_ERROR),
        (CLINotFoundError("none"), Outcome.INVALID_CALL_CONFIG),
        (ValueError("bad"), Outcome.INVALID_CALL_CONFIG),
        (ConnectionResetError("reset"), Outcome.TRANSPORT_ERROR),
        (RuntimeError("?"), Outcome.OTHER_FAILURE),
    ],
)
def test_failure_table(tmp_path: Path, exc: BaseException, outcome: Outcome) -> None:
    assert classify_failure(exc) is outcome
    res = asyncio.run(_agent(tmp_path, exc=exc).invoke(_request()))
    assert res.outcome is outcome
    assert type(exc).__name__ in res.stop_reason


def test_close_is_noop(tmp_path: Path) -> None:
    asyncio.run(_agent(tmp_path).close())


def test_fake_agent_counts_and_limit() -> None:
    agent = doubles.FakeAgent([StageResult(Outcome.DONE, "x", None, "end")])
    agent.fail_with_limit()
    first = asyncio.run(agent.invoke(_request()))
    second = asyncio.run(agent.invoke(_request()))
    third = asyncio.run(agent.invoke(_request()))
    assert first.outcome is Outcome.SUBSCRIPTION_LIMIT
    assert (second.text, third.text) == ("x", "ok")
    assert agent.invocations == 3
    asyncio.run(agent.close())
    assert agent.closed


def test_other_fakes() -> None:
    rev = doubles.FakeReviewer([ReviewVerdict(False, "no", "r1")])
    assert asyncio.run(rev.review(_request())).approved is False
    assert asyncio.run(rev.review(_request())).reviewer_id == "fake-reviewer"
    assert rev.invocations == 2

    git = doubles.FakeGit("d")
    git.stage_paths(Path("."), ["a"])
    assert git.diff(Path(".")) == "d"
    assert git.commit(Path("."), "m") == "fake0001"
    git.rollback(Path("."))
    assert git.invocations == 3

    runner = doubles.FakeTestRunner([1])
    assert runner.run(Path("."), "t").returncode == 1
    assert runner.run(Path("."), "t").returncode == 0
    assert runner.invocations == 2

    clock = doubles.FakeClock(1.0)
    clock.advance(2)
    assert (clock.now(), clock.monotonic()) == (3.0, 3.0)
