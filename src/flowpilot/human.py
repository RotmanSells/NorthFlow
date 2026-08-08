"""Вопросы человеку: ожидание ответов, сохранение в state."""
from __future__ import annotations

import click

from .state import ProjectState


def ask_questions(state: ProjectState, questions: list[str]) -> dict:
    """Задаёт вопросы по одному, сохраняет ответы в state.answers."""
    if not questions:
        return state.answers
    click.echo("\n--- Вопросы перед продолжением ---")
    for i, q in enumerate(questions, 1):
        if isinstance(q, dict):
            q_text = q.get("question", str(q))
            key = q.get("key", f"q{i}")
        else:
            q_text = str(q)
            key = f"q{i}"
        answer = click.prompt(f"Q{i}: {q_text}", default="", show_default=False)
        if answer:
            state.answers[key] = answer
    state.save()
    return state.answers
