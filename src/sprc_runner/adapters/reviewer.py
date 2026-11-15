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
    "с чек-листом. В ответе ровно одна строка-вердикт из одного слова: "
    f"{APPROVED} (замечаний нет) или {REJECTED} (есть замечания) — "
    "предпочтительно первой строкой; остальные строки — комментарии, "
    "и слова вердиктов в них не используй."
)


def parse_verdict(text: str) -> tuple[bool, str]:
    """Строка-вердикт ищется во всём ответе; неоднозначность — отказ.

    Серия 4 (2026-11-15): модель иногда ставит преамбулу перед вердиктом,
    и честный APPROVED засчитывался отказом, пока разбиралась только первая
    строка. Вердикт — отдельная строка со словом APPROVED или REJECTED;
    если таких строк нет или они противоречат друг другу, ревью
    не пропускает (fail-closed).
    """
    found: list[str] = []
    comments: list[str] = []
    for line in text.strip().splitlines():
        token = line.strip().strip("*`#:. ").upper()
        if token in (APPROVED, REJECTED):
            found.append(token)
        else:
            comments.append(line)
    verdicts = set(found)
    if verdicts == {APPROVED}:
        return True, "\n".join(comments).strip()
    if verdicts == {REJECTED}:
        return False, "\n".join(comments).strip()
    if not found:
        return False, f"вердикт не разобран: {text.strip()}"
    return False, f"вердикт противоречив: {text.strip()}"


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
