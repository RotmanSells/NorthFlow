"""Вопросы человеку: выбор вариантов, сохранение ответов в state."""
from __future__ import annotations

import click

from .state import ProjectState

DEFAULT_OPTIONS = [
    {"value": "да", "label": "Да"},
    {"value": "нет", "label": "Нет"},
    {"value": "не знаю", "label": "Не знаю / пусть решит агент"},
]


def _render_question(q: dict, idx: int) -> tuple[str, str, list[dict]]:
    """Нормализует вопрос: текст, ключ, варианты."""
    key = str(q.get("key") or f"q{idx}")
    text = str(q.get("question") or q.get("text") or str(q))
    options = q.get("options") or q.get("answers")
    if isinstance(options, list) and options and all(isinstance(o, str) for o in options):
        options = [{"value": o, "label": o} for o in options]
    if not options:
        options = list(DEFAULT_OPTIONS)
    return text, key, options


def ask_questions(state: ProjectState, questions: list) -> dict:
    """Задаёт вопросы с вариантами выбора, ответы сохраняет в state.answers."""
    if not questions:
        return state.answers
    click.echo("\n--- Вопросы перед продолжением ---")
    for i, q in enumerate(questions, 1):
        text, key, options = _render_question(q, i)
        click.echo(f"\nQ{i}: {text}")
        for j, opt in enumerate(options, 1):
            label = opt.get("label", opt.get("value", str(opt)))
            click.echo(f"  {j}. {label}")
        choices = [opt.get("value", opt.get("label", str(opt))) for opt in options]
        # Всегда добавляем свободный ввод как последний вариант
        choices.append("__custom__")
        choice = click.prompt(
            "Выбери вариант (номер, или свой ответ)",
            type=click.Choice([str(k) for k in range(1, len(options) + 1)] + ["__custom__"]),
            default="1",
            show_default=True,
        )
        if choice == "__custom__":
            answer = click.prompt("Свой ответ", default="", show_default=False)
            if not answer:
                answer = "не знаю"
        else:
            answer = choices[int(choice) - 1]
        state.answers[key] = answer
        state.memory[f"question:{key}"] = answer
    state.save()
    return state.answers


def questions_from_result(result: str | dict) -> list:
    """Достаёт список вопросов из результата роли (raw text или payload)."""
    if isinstance(result, dict):
        qs = result.get("questions") or result.get("clarifying_questions") or []
        if isinstance(qs, list):
            return qs
        return []
    import json
    import re
    m = re.search(r"\{.*\}", result, re.DOTALL)
    if not m:
        return []
    try:
        payload = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    qs = payload.get("questions") or payload.get("clarifying_questions") or []
    return qs if isinstance(qs, list) else []
