"""Ревьюер: реализация `ReviewerPort` отдельной инвокацией модели (Q-03, P-02).

Ревью выполняет модель Claude через единственный адаптер SDK (`AgentPort`) со
stage `review` и моделью `model_review`; собственного обращения к SDK здесь нет.
Решение «не одобрено» принимается по умолчанию: любой отказ инвокации или
неразборчивый ответ ревью не пропускает.
"""

from dataclasses import replace

from sprc_runner.ports import AgentPort, Outcome, ReviewVerdict, StageRequest

APPROVED = "APPROVED"
REJECTED = "REJECTED"
INSTRUCTION = (
    "Проведи ревью реализации задачи: прочитай изменённые файлы и сопоставь их "
    "с чек-листом. Первой строкой ответа напиши ровно одно слово: "
    f"{APPROVED} (замечаний нет) или {REJECTED} (есть замечания); "
    "со следующей строки — комментарии."
)


def parse_verdict(text: str) -> tuple[bool, str]:
    """Первая непустая строка — вердикт; всё остальное — комментарии."""
    lines = text.strip().splitlines()
    if not lines:
        return False, "пустой ответ ревью"
    head = lines[0].strip().strip("*`#. ").upper()
    rest = "\n".join(lines[1:]).strip()
    if head == APPROVED:
        return True, rest
    if head == REJECTED:
        return False, rest
    return False, f"вердикт не разобран: {text.strip()}"


class SdkReviewer:
    """`ReviewerPort` поверх `AgentPort`: модель этапа берётся из запроса."""

    def __init__(self, agent: AgentPort) -> None:
        self._agent = agent

    async def review(self, request: StageRequest) -> ReviewVerdict:
        asked = replace(request, inputs=(*request.inputs, INSTRUCTION))
        result = await self._agent.invoke(asked)
        reviewer_id = f"model:{request.model}"
        if result.outcome is not Outcome.DONE:
            return ReviewVerdict(
                False,
                f"ревью не выполнено: {result.outcome.value}: {result.stop_reason}",
                reviewer_id,
            )
        approved, comments = parse_verdict(result.text)
        return ReviewVerdict(approved, comments, reviewer_id)
