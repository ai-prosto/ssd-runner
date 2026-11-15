# run: самопорождённая «грязь» в tasks.md клинит следующий запуск, а run-level rescue прячет правки оператора в стэш даже когда прогон отказался стартовать

## Среда

- spec-runner из `~/.local/share/uv/tools/spec-runner` (установлен uv tool), репозиторий цели `sdd-runer`, режим `--integration-pr --strict`, git-автоматизация включена.
- Наблюдалось 2026-11-14 ~19:21–19:45 на живом прогоне воркстрима `sprc-runner-claude-agent-sdk-20261112-eng-` (вызовы через dfl, но оба дефекта воспроизводятся самим spec-runner).

## Симптом, как его видит оператор

1. `spec-runner run --all --strict --spec-prefix=... --integration-pr` завершается с кодом 1:

   ```
   ⛔ Refusing to run: spec/config files have uncommitted changes:
       M spec/sprc-runner-claude-agent-sdk-20261112-eng-tasks.md
      Commit them first (the spec is the run's contract), or override
      with --allow-dirty-spec.
   ```

   При этом «грязь» — это не правка оператора, а одна строка, которую **сам
   раннер** записал на предыдущем запуске:

   ```diff
   ### TASK-018: Регламенты: приём изменений и серия надёжности
   -P2 | TODO   Est: 0.5d
   +P2 | ⬜ TODO   Est: 0.5d
   ```

   Статус не изменился (`todo` → `todo`), поменялась только нормализация
   (добавлен эмодзи). Повторный `run` падает тем же отказом — замкнутый круг:
   раннер сам пишет «грязь», сам же из-за неё отказывается работать, а
   оператору предлагает закоммитить изменение, которого тот не делал
   (ровно то, от чего защищался #192).

2. Перед этим отказом run-level rescue уже **спрятал в стэш реальные правки
   оператора** (7 путей: три изменённых `workstreams/**/spec/*.md` и
   нетрекнутые `tests/manual/*`):

   ```
   [19:43:12] 📦 stashed 7 uncommitted path(s) before starting (…,
   workstreams/.../15-behaviour-spec.md, .../25-acceptance.md,
   .../30-decomposition.md, tests/manual/change-review-checklist.md, and 1 more)
   as “spec-runner rescue: run at 2026-11-14T19:43:12”
   ```

   Итог для оператора: прогон не состоялся (exit 1), но рабочее дерево
   осталось «обчищенным» — правки молча уехали в `stash@{0}`, об этом одна
   строка в progress-файле, на экране ничего. Обнаружили случайно, через
   `git stash list`.

   Хронология (из `.executor-progress.txt`, task-history и `git`):

   ```
   19:43:12  stash push 7 путей (rescue: run) — на ветке spec-runner/run-20261114-173445
   19:43:12  TASK-018 | status -> todo        (reset-failed пишет tasks.md)
   19:44:56  ⛔ Refusing to run: spec/config files are not committed [M tasks.md]
   exit 1;   чекаут оставлен на main, правки оператора — только в стэше
   ```

## Дефект 1: нормализация статуса без смены статуса создаёт «грязь», которую не признаёт recover_interrupted_flip

`task.py::update_task_status` при записи в файл с plain-форматом строки всегда
инжектит эмодзи:

```python
else:
    # Plain format (no emoji): inject emoji and update status text
    new_line = re.sub(
        r"\|\s*(TODO|IN_PROGRESS|REVIEW|DONE|BLOCKED)",
        f"| {new_emoji} {new_status.upper()}",
        line, count=1, flags=re.IGNORECASE,
    )
```

То есть запись `todo` поверх `TODO` даёт `⬜ TODO` — файл меняется при
неизменном статусе. Дальше `cli.py::_enforce_clean_spec` зовёт
`bookkeeping.recover_interrupted_flip`, но тот лечит только **переход**
статуса (`status_only_transition`: `flip is None` при равных статусах), а
`_split_meta` при сравнении строк эмодзи отбрасывает:

```python
prefix, _emoji, word, rest = match.groups()
return word.lower(), f"{prefix}\0{rest}"
```

Получается: доказательство «изменился только статусный токен» проходит, но
перехода нет (`todo == todo`) — значит, recovery молчит, и guard отказывает.
Нормализация, авторство которой доказуемо принадлежит харнессу, остаётся
«грязью» навсегда.

### Минимальный репро (без dfl, на любом репо с plain-форматом строки)

```bash
git init demo && cd demo
mkdir spec
cat > spec/tasks.md <<'EOF'
# Tasks

### TASK-001: Demo
P2 | TODO   Est: 1d

**Checklist:**
- [ ] a
EOF
git add -A && git commit -m init

python3 - <<'EOF'
from pathlib import Path
from spec_runner import task
# любой путь, который приводит к записи того же статуса:
# reset-failed, повторная запись todo и т.п.
task.update_task_status(Path("spec/tasks.md"), "TASK-001", "todo")
EOF

git diff --stat   # tasks.md изменён: "P2 | TODO" -> "P2 | ⬜ TODO"
spec-runner run --all   # ⛔ Refusing to run: spec/config files have uncommitted changes
```

### Предлагаемое исправление (вариант A — не писать no-op)

Самое дешёвое: `update_task_status` не должен трогать файл, если статусное
слово уже равно целевому — идемпотентная запись:

```python
# task.py, update_task_status, до перезаписи строки
current = _status_of_line(line)  # разбор тем же TASK_META
if current == new_status.lower():
    return True  # статус уже такой; файл не трогаем,
    # нормализацию не навязываем
```

Вариант B (если нормализацию хочется сохранить): научить
`recover_interrupted_flip` признавать «same-status, formatting-only» дифф —
`_split_meta` уже отбрасывает эмодзи, достаточно, чтобы
`status_only_transition` возвращал flip и при `previous == new`, а
`commit_status_flip` коммитил его с reason «normalized by harness». Вариант A
проще и убирает причину, вариант B лечит уже застрявшие деревья.

## Дефект 2: rescue-then-refuse — отказавшийся прогон не возвращает спасённое

`hooks.py::rescue_run_uncommitted` стэшит всё незакоммиченное (кроме
`spec_contract_paths`) **до** того, как прогон доказал, что вообще стартует.
Если после rescue срабатывает любой отказ (`_enforce_clean_spec`, state/spec
mismatch и т.п.), процесс выходит с кодом 1, а дерево остаётся без правок
оператора: «спасение» превратилось в молчаливое исчезновение. Контракт из
docstring самого rescue — «destroying work is never the fallback» — в этом
сценарии нарушается по духу: байты живы в стэше, но оператор об этом не знает,
а прогона, ради чистоты которого всё затевалось, не было.

Дополнительная несогласованность: guard и rescue по-разному понимают «спеку».
`rescue_run_uncommitted` исключает только `spec_contract_paths(config)`
(`spec/*.md`, конфиги), а спек-узлы воркстрима `workstreams/**/spec/*.md` —
для governance такая же спека — уходят в стэш как «stray work».

### Предлагаемое исправление

1. Переставить порядок в `cli.py` команды `run`: все fail-closed guards
   (`_enforce_clean_spec`, `_enforce_untracked_state`, state/spec mismatch)
   выполняются **до** первой мутации дерева; `rescue_run_uncommitted`
   вызывается последним, непосредственно перед первым деструктивным git-шагом
   (checkout/fork интеграционной ветки).
2. Если прогон завершился, не начав ни одной задачи (любой отказ после
   rescue), — откатывать rescue обратно:

   ```python
   # псевдокод вокруг существующего run-flow
   rescued_label = rescue_run_uncommitted(config)  # вернуть label стэша
   try:
       _start_run(...)  # до первой задачи
   except RunRefused:
       if rescued_label:
           _git(config, "stash", "pop")  # инвариант: несостоявшийся
       raise  # старт не меняет дерево
   ```

   Инвариант, который стоит зафиксировать тестом: *прогон, завершившийся до
   старта первой задачи, оставляет рабочее дерево байт-в-байт как нашёл*.
3. На любом отказе после rescue печатать label стэша в stderr рядом с
   `⛔`-сообщением, а не только в progress-файл: оператор должен увидеть, куда
   делись его файлы, в том же экране, где прочитал отказ.

## Почему это больно в связке с dfl

dfl запускает spec-runner не в интерактивном терминале: оператор видит только
«шаг завершился с кодом 1». Стэш-«спасение» и самопорождённая грязь вместе
дают сценарий: оператор правит governance-узлы → запускает конвейер → правки
исчезают из дерева, прогон не стартует, причина — строка, которую раннер сам
же и записал. Восстановление потребовало ручной археологии по
`git stash list` / `.executor-progress.txt`.

## Куски evidence

- `git stash list`:
  `stash@{0}: On spec-runner/run-20261114-173445: spec-runner rescue: run at 2026-11-14T19:43:12`
- task-history: `2026-11-14T19:43:12 | TASK-018 | status -> todo`
- `git diff spec/...-tasks.md` на `main`: единственный hunk `P2 | TODO` → `P2 | ⬜ TODO` (TASK-018).
- Отказ: `cli.py::_enforce_clean_spec` → `Refusing to run: spec/config files are not committed, files=[' M spec/sprc-runner-claude-agent-sdk-20261112-eng-tasks.md']`.
