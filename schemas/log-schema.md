# Схема журнала `log.jsonl`

**Версия схемы: 1.0.** Носитель — JSONL: одна запись-событие на строку, только
добавление в конец; каждая запись сбрасывается на диск сразу (переживает аварийную
остановку). Единственный читатель — `sprc_runner.core.logbook.parse_log`.

## Конверт (каждая запись)

| Поле | Тип | Описание |
|---|---|---|
| `schema_version` | строка | версия схемы, `МАЖОР.МИНОР` |
| `run_id` | строка | идентификатор прогона |
| `seq` | целое | порядковый номер записи в прогоне, с 1 |
| `ts` | число | отметка времени, секунды Unix |
| `event` | строка | тип события из закрытого словаря |
| `stage` | строка | этап, если определён |
| `task_id` | строка | идентификатор задачи, если определён |
| `attempt` | целое | номер попытки, если определён |
| `model` | строка | идентификатор применённой модели, если определён |

Необязательные поля конверта опускаются, если не определены.

## Типы событий (закрытый словарь)

`run_started`, `run_finished`, `stage_started`, `stage_finished`, `task_started`,
`attempt_started`, `step_finished`, `diff_verdict`, `attempt_finished`,
`task_finished`, `run_paused`.

## Длительности

События `run_finished`, `stage_finished`, `task_finished`, `attempt_finished`
несут явные поля `started_at`, `finished_at` (секунды Unix) и `duration_s`
(секунды). Читатель не вычисляет их из разности отметок.

## Версионирование

Добавление поля — минорное расширение (читатель мажора 1 принимает `1.x`).
Смена смысла или удаление поля — новая мажорная версия; читатель отвергает
чужой мажор с `LogFormatError`. Необязательные поля токенов и стоимости (Q-14),
если появятся, добавляются в полезную нагрузку событий этапа и попытки.

## Полезная нагрузка событий задачи (минорное расширение 1.x)

- `step_finished` (`stage` = `implement`/`review`/`tests`): `model` у шагов
  реализации и ревью; у `review` — `approved`, `reviewer_id`; у `tests` —
  `returncode`, `command`, `output_ref` фактического запуска раннером.
- `attempt_finished`: `model`, `model_review`, `success`, `error_code`
  (при неуспехе), `decision`, `review_approved`, `tests_returncode`, `tests_command`.
- `stage_started`/`stage_finished` этапов подготовки несут `model`.
- `task_finished` — строка состава FR-06: `started_at`, `task_id`, `model`,
  `tests_returncode`, `review_approved`, `attempt` (номер итоговой попытки),
  `attempts_total`, `success`, `error_code` (обязателен при неуспехе), `decision`.

Проекция `project_tasks` и проверка `completeness(log_text, task_ids)` читают только
`log.jsonl` и идентификаторы из `tasks.md`; пропуск любого поля или задачи — дефект.
