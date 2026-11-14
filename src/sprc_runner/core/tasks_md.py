"""`tasks.md` как внешний формат: разметка записей — как в spec-runner (BEH-27).

Собственных полей нет: читаются и пишутся только те конструкции, что знает
spec-runner — заголовок `### ID: Название`, строка меты `P2 | ⬜ TODO   Est: 1d`,
чекбоксы, `**Depends on:**`, `**Traces to:**`. Запись статуса и отметки меняет
только свою строку и не перерисовывает остальное, поэтому файл остаётся
разбираемым тем же инструментарием, что файл соседнего репозитория.
"""

import re
from dataclasses import dataclass, field

ID_PATTERN = r"[A-Z][A-Z0-9]*-\d+"
TASK_HEADER = re.compile(rf"^### ({ID_PATTERN}): (.+)$")
TASK_REF = re.compile(rf"\[({ID_PATTERN})\]")
STATUS_WORDS = ("TODO", "IN_PROGRESS", "REVIEW", "DONE", "BLOCKED")
TASK_META = re.compile(
    r"^(?:[ \t]*[-*]\s+)?"
    r"(?:(?:🔴|🟠|🟡|🟢)\s+)?(P\d)\s*\|\s*(?:(?:⬜|🔄|🔍|✅|⏸️)\s+)?"
    rf"((?i:{'|'.join(STATUS_WORDS)}))\b"
)
CHECKLIST_ITEM = re.compile(r"^- \[([ x])\] (.+)$")
DEPENDS_ON = re.compile(r"\*\*Depends on:\*\* (.+)")
TRACES_TO = re.compile(r"\*\*Traces to:\*\* (.+)")
ESTIMATE = re.compile(r"Est: (\d+(?:\.\d+)?(?:[-–]\d+(?:\.\d+)?)?[dh])")

STATUS_EMOJI = {
    "todo": "⬜",
    "in_progress": "🔄",
    "review": "🔍",
    "done": "✅",
    "blocked": "⏸️",
}
_EMOJI_RE = "|".join(re.escape(e) for e in STATUS_EMOJI.values())
_STATUS_RE = re.compile(rf"\|\s*(?:(?:{_EMOJI_RE})\s+)?(?i:{'|'.join(STATUS_WORDS)})")


class TasksFormatError(ValueError):
    """Файл задач не соответствует формату spec-runner."""


@dataclass
class TaskRecord:
    id: str
    name: str
    priority: str
    status: str
    estimate: str = ""
    checklist: list[tuple[str, bool]] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    traces_to: list[str] = field(default_factory=list)
    has_meta: bool = False

    @property
    def is_runnable(self) -> bool:
        """Пригодна входом цикла: есть мета, не закрыта, есть что выполнять."""
        return (
            self.has_meta
            and self.status not in ("done", "blocked")
            and any(not checked for _, checked in self.checklist)
        )


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            nl = text.find("\n", end + 1)
            return text[nl + 1 :] if nl != -1 else ""
    return text


def parse_tasks(text: str) -> list[TaskRecord]:
    """Разобрать `tasks.md` в записи в порядке файла."""
    tasks: list[TaskRecord] = []
    current: TaskRecord | None = None
    in_checklist = False
    for line in _strip_frontmatter(text).split("\n"):
        header = TASK_HEADER.match(line)
        if header:
            current = TaskRecord(header[1], header[2], "p0", "todo")
            tasks.append(current)
            in_checklist = False
            continue
        if current is None:
            continue
        meta = TASK_META.match(line)
        if meta:
            current.priority = meta[1].lower()
            current.status = meta[2].lower()
            current.has_meta = True
            est = ESTIMATE.search(line)
            if est:
                current.estimate = est[1]
            continue
        if line.startswith("**Checklist:**"):
            in_checklist = True
            continue
        if in_checklist:
            item = CHECKLIST_ITEM.match(line)
            if item:
                current.checklist.append((item[2], item[1] == "x"))
                continue
            if line.startswith("**"):
                in_checklist = False
        if m := DEPENDS_ON.search(line):
            current.depends_on = TASK_REF.findall(m[1])
        elif m := TRACES_TO.search(line):
            current.traces_to = [r.strip("[] ") for r in m[1].split(",") if r.strip()]
    return tasks


def validate_tasks(text: str) -> list[TaskRecord]:
    """Разобрать и проверить: ≥1 задача, у каждой мета, ≥1 пригодна циклу."""
    tasks = parse_tasks(text)
    if not tasks:
        raise TasksFormatError("в списке нет ни одной задачи `### ID: Название`")
    ids = [t.id for t in tasks]
    if len(set(ids)) != len(ids):
        raise TasksFormatError("идентификаторы задач повторяются")
    for t in tasks:
        if not t.has_meta:
            raise TasksFormatError(f"{t.id}: нет строки меты `P2 | TODO`")
    if not any(t.is_runnable for t in tasks):
        raise TasksFormatError("нет задачи, пригодной входом цикла реализации")
    return tasks


def _task_bounds(lines: list[str], task_id: str) -> tuple[int, int]:
    start = next(
        (
            i
            for i, ln in enumerate(lines)
            if (m := TASK_HEADER.match(ln)) and m[1] == task_id
        ),
        None,
    )
    if start is None:
        raise TasksFormatError(f"задачи {task_id} нет в файле")
    end = next(
        (j for j in range(start + 1, len(lines)) if TASK_HEADER.match(lines[j])),
        len(lines),
    )
    return start, end


def set_status(text: str, task_id: str, status: str) -> str:
    """Перезаписать статус задачи в строке меты; остальной текст не трогается."""
    if status not in STATUS_EMOJI:
        raise TasksFormatError(f"неизвестный статус {status!r}")
    lines = text.split("\n")
    start, end = _task_bounds(lines, task_id)
    for j in range(start + 1, end):
        if TASK_META.match(lines[j]):
            lines[j] = _STATUS_RE.sub(
                f"| {STATUS_EMOJI[status]} {status.upper()}", lines[j], count=1
            )
            return "\n".join(lines)
    raise TasksFormatError(f"{task_id}: нет строки меты")


def set_checklist(text: str, task_id: str, index: int, checked: bool) -> str:
    """Поставить или снять отметку `[x]` у пункта чеклиста задачи."""
    lines = text.split("\n")
    start, end = _task_bounds(lines, task_id)
    seen = 0
    for j in range(start + 1, end):
        item = CHECKLIST_ITEM.match(lines[j])
        if item:
            if seen == index:
                lines[j] = f"- [{'x' if checked else ' '}] {item[2]}"
                return "\n".join(lines)
            seen += 1
    raise TasksFormatError(f"{task_id}: нет пункта чеклиста {index}")


def mark_completed(text: str, task_id: str) -> str:
    """Отметка о выполнении в формате spec-runner: `✅ DONE` и все пункты `[x]`.

    Меняются только строка меты и чекбоксы задачи; остальной текст не трогается.
    """
    text = set_status(text, task_id, "done")
    lines = text.split("\n")
    start, end = _task_bounds(lines, task_id)
    for j in range(start + 1, end):
        item = CHECKLIST_ITEM.match(lines[j])
        if item:
            lines[j] = f"- [x] {item[2]}"
    return "\n".join(lines)


def render_task(task: TaskRecord) -> str:
    """Записать задачу в разметке spec-runner."""
    emoji = STATUS_EMOJI[task.status]
    est = f"   Est: {task.estimate}" if task.estimate else ""
    out = [
        f"### {task.id}: {task.name}",
        f"{task.priority.upper()} | {emoji} {task.status.upper()}{est}",
        "",
    ]
    if task.depends_on:
        out.append("**Depends on:** " + ", ".join(f"[{d}]" for d in task.depends_on))
    if task.checklist:
        out += ["", "**Checklist:**"]
        out += [f"- [{'x' if c else ' '}] {t}" for t, c in task.checklist]
    if task.traces_to:
        out += ["", "**Traces to:** " + ", ".join(f"[{r}]" for r in task.traces_to)]
    return "\n".join(out) + "\n"


def render_tasks(tasks: list[TaskRecord]) -> str:
    return "\n".join(render_task(t) for t in tasks)
