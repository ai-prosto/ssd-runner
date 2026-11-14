# sprc-runner (Claude Agent SDK)

Аналог sprc-runner на Claude Agent SDK. Стек: Python 3.12+, uv, anyio.

## Проверки качества

Работают локально, без ключей доступа, платной инфраструктуры и внешних сервисов:

```sh
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pyrefly check
uv run pytest
```
