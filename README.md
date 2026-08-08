# FlowPilot

Маленький движок для процесса: идея → исследование → вопросы человеку → архитектура → критика → документация → этапы → 3-5 задач → реализация → проверки → review → commit → обновление state.

## Быстрый старт

```bash
cd engine
python -m venv .venv && source .venv/bin/activate
pip install -e .
flowpilot init ~/projects/my-app
```

Создай конфиг `~/.flowpilot/config.json` (или положи рядом `flowpilot.json`):

```json
{
  "provider": "openai",
  "base_url": "https://api.openai.com/v1",
  "api_key": "sk-...",
  "model": "gpt-4.1-mini"
}
```

## Команды

- `flowpilot init <dir>` — создать структуру проекта.
- `flowpilot run <dir>` — выполнить следующий шаг конвейера.
- `flowpilot preflight <dir>` — проверить branch/dirty/docs перед запуском.
- `flowpilot status <dir>` — текущая фаза, этапы, задачи.
- `flowpilot roadmap <dir>` — перегенерировать roadmap.md.

## Правила процесса

- Вертикальные срезы: каждый этап закрывает пользовательский сценарий.
- Задачи по 2-3к строк, логичные, не рваные.
- Детально планируются только 3-5 ближайших задач.
- После каждой задачи — commit, обновление roadmap и папки этапа.
- Если задача противоречит архитектуре — агент обязан остановиться и спросить с меткой `[КРИТИЧЕСКОЕ ИЗМЕНЕНИЕ]`.
- Scope-контроль: запись разрешена только в allowed paths роли.
