"""Единственный адаптер Claude Agent SDK и таблица классификации отказов (Q-10).

Это единственный модуль, импортирующий `claude_agent_sdk` (BEH-36). Типы и
исключения SDK наружу не выходят: на границе — `StageRequest`/`StageResult`.
"""

import json
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

import anyio
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKError,
    CLIConnectionError,
    CLIJSONDecodeError,
    CLINotFoundError,
    ProcessError,
    ResultMessage,
    TextBlock,
    query,
)

from sprc_runner.ports import Outcome, StageRequest, StageResult

QueryFn = Callable[..., AsyncIterator[Any]]

LIMIT_MARKERS = (
    "usage limit",
    "rate limit",
    "limit reached",
    "quota",
    "overloaded",
)
LIMIT_STATUS = re.compile(r"\b429\b")
"""Сигнатуры текста отказа, означающие исчерпание лимитов подписки."""

# Таблица «исключение SDK → доменный исход»; проверяется по порядку,
# ProcessError дополнительно разбирается по тексту (см. `classify_failure`).
FAILURE_TABLE: tuple[tuple[type[BaseException], Outcome], ...] = (
    (CLINotFoundError, Outcome.INVALID_CALL_CONFIG),
    (CLIConnectionError, Outcome.TRANSPORT_ERROR),
    (CLIJSONDecodeError, Outcome.TRANSPORT_ERROR),
    (ProcessError, Outcome.OTHER_FAILURE),
    (ClaudeSDKError, Outcome.OTHER_FAILURE),
)


def _is_limit_text(text: str) -> bool:
    lowered = text.lower()
    return any(m in lowered for m in LIMIT_MARKERS) or bool(LIMIT_STATUS.search(text))


def classify_failure(exc: BaseException) -> Outcome:
    """Перевести исключение SDK в доменный исход по единой таблице."""
    if _is_limit_text(str(exc)):
        return Outcome.SUBSCRIPTION_LIMIT
    for exc_type, outcome in FAILURE_TABLE:
        if isinstance(exc, exc_type):
            return outcome
    if isinstance(exc, (ValueError, TypeError)):
        return Outcome.INVALID_CALL_CONFIG
    if isinstance(exc, OSError):
        return Outcome.TRANSPORT_ERROR
    return Outcome.OTHER_FAILURE


def _to_plain(message: Any) -> Any:
    if is_dataclass(message) and not isinstance(message, type):
        return {"type": type(message).__name__, **asdict(message)}
    return {"type": type(message).__name__, "repr": repr(message)}


class ClaudeSdkAgent:
    """Реализация `AgentPort` поверх `claude_agent_sdk.query`."""

    def __init__(self, transcript_dir: Path, query_fn: QueryFn | None = None) -> None:
        self._transcript_dir = transcript_dir
        self._query = query_fn or query
        self._counter = 0

    def _transcript_path(self, request: StageRequest) -> Path:
        self._counter += 1
        stage = re.sub(r"[^A-Za-z0-9_.-]", "_", request.stage)
        return self._transcript_dir / f"{stage}-{self._counter:04d}.jsonl"

    async def invoke(self, request: StageRequest) -> StageResult:
        """Выполнить инвокацию этапа и вернуть доменный результат."""
        path = self._transcript_path(request)
        records: list[Any] = []
        texts: list[str] = []
        final: ResultMessage | None = None
        failure: BaseException | None = None
        timed_out = False
        try:
            options = ClaudeAgentOptions(
                model=request.model,
                cwd=request.workdir,
                allowed_tools=list(request.allowed_tools),
            )
            prompt = "\n\n".join(request.inputs)
            with anyio.fail_after(request.timeout_seconds):
                async for message in self._query(prompt=prompt, options=options):
                    records.append(_to_plain(message))
                    if isinstance(message, AssistantMessage):
                        texts.extend(
                            b.text for b in message.content if isinstance(b, TextBlock)
                        )
                    elif isinstance(message, ResultMessage):
                        final = message
        except TimeoutError:
            timed_out = True
        except Exception as exc:  # граница: ничего из SDK не пробрасываем
            failure = exc
        ref = self._write_transcript(path, records)
        if timed_out:
            return StageResult(Outcome.OTHER_FAILURE, "\n".join(texts), ref, "timeout")
        if failure is not None:
            return StageResult(
                classify_failure(failure),
                "\n".join(texts),
                ref,
                f"{type(failure).__name__}: {failure}",
            )
        return self._from_result(texts, final, ref)

    def _from_result(
        self, texts: list[str], final: ResultMessage | None, ref: str | None
    ) -> StageResult:
        if final is None:
            return StageResult(
                Outcome.OTHER_FAILURE, "\n".join(texts), ref, "no_result"
            )
        text = final.result if final.result is not None else "\n".join(texts)
        if final.is_error:
            outcome = (
                Outcome.SUBSCRIPTION_LIMIT
                if _is_limit_text(text)
                else Outcome.OTHER_FAILURE
            )
            return StageResult(outcome, text, ref, final.subtype, final.usage)
        return StageResult(Outcome.DONE, text, ref, final.subtype, final.usage)

    def _write_transcript(self, path: Path, records: list[Any]) -> str | None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as fh:
                for rec in records:
                    fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        except OSError:
            return None
        return str(path)

    async def close(self) -> None:
        """Освободить ресурсы (у `query` сессий между вызовами нет)."""
