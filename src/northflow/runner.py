"""Шаги конвейера, общие для CLI и веб-интерфейса."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from .checks import PreflightError, preflight
from .config import RuntimeConfig, load_config
from .human import ask_questions, questions_from_result
from .pipeline import cmd_step, make_client, preflight_or_stop
from .roles import extract_json
from .state import ProjectState


def _print_banner(title: str, payload: dict, max_len: int = 2000) -> str:
    """Возвращает текст результата шага для вывода в CLI/веб."""
    return f"{title}:\n{json.dumps(payload, ensure_ascii=False, indent=2)[:max_len]}"


def run_phase_step(root: Path, cfg: RuntimeConfig | None = None, state: ProjectState | None = None,
                   config_path: str | None = None, expected_branch: str = "main", on_event=None,
                   request_approval=None) -> dict:
    """Выполняет один шаг конвейера. Возвращает результат для интерфейса.

    Если агент задал вопросы — они сохраняются в state.pending_questions,
    фаза НЕ продвигается до ответа.
    """
    cfg = cfg or load_config(config_path)
    root = Path(root)
    state = state or ProjectState.load(root)
    client = make_client(cfg)
    result: dict = {"ok": True, "message": "", "payload": None, "phase": state.phase}

    try:
        phase = state.phase
        if phase == "idea":
            if on_event:
                on_event("phase", {"name": "researcher"})
            step = cmd_step(client, cfg, state, "researcher",
                "Изучи docs/00-idea.md, проведи исследование, задай вопросы человеку.", on_event=on_event,
                request_approval=request_approval)
            result["message"] = _print_banner("ИССЛЕДОВАНИЕ", step["payload"])
            result["payload"] = step["payload"]
            qs = questions_from_result(step["result"])
            if qs:
                state.pending_questions = qs
                state.pending_next_phase = "architecture"
                state.save()
                result["questions"] = qs
                result["message"] += "\n\nЕсть вопросы — ждём ответа человека."
            else:
                state.phase = "architecture"
                state.save()
        elif phase == "architecture":
            if on_event:
                on_event("phase", {"name": "architect"})
            step = cmd_step(client, cfg, state, "architect",
                "Составь полную документацию и AGENTS.md. Если нужны решения — задай вопросы человеку.", on_event=on_event,
                request_approval=request_approval)
            result["message"] = _print_banner("АРХИТЕКТУРА", step["payload"])
            result["payload"] = step["payload"]
            qs = questions_from_result(step["result"])
            if qs:
                state.pending_questions = qs
                state.pending_next_phase = "architecture"
                state.save()
                result["questions"] = qs
                result["message"] += "\n\nЕсть вопросы — ждём ответа человека."
            else:
                if on_event:
                    on_event("phase", {"name": "critic"})
                crit = cmd_step(client, cfg, state, "critic",
                    "Проверь созданную архитектуру, дай вердикт.", on_event=on_event,
                    request_approval=request_approval)
                result["message"] += "\n\n" + _print_banner("КРИТИК", crit["payload"])
                state.phase = "roadmap"
                state.save()
        elif phase == "roadmap":
            if on_event:
                on_event("phase", {"name": "planner"})
            step = cmd_step(client, cfg, state, "planner",
                "Составь этапы и 3-5 детальных задач для первого этапа.", on_event=on_event,
                request_approval=request_approval)
            result["message"] = _print_banner("ПЛАНИРОВЩИК", step["payload"])
            result["payload"] = step["payload"]
            from .planning import validate_plan
            errors = validate_plan(state)
            if errors:
                result["ok"] = False
                result["message"] = "ПЛАН НЕ ПОЛНЫЙ:\n" + "\n".join(errors[:20])
                return result
            state.phase = "implementation"
            state.save()
        elif phase == "implementation":
            preflight_or_stop(state, cfg)
            task = state.next_task()
            if not task:
                result["message"] = "Все задачи этапа завершены. Закрой этап вручную в state."
                return result
            from .implementation import TaskEngine
            eng = TaskEngine(state, cfg, client)
            cycle = eng.complete_task_cycle(task, on_event=on_event, request_approval=request_approval)
            result["message"] = "ЦИКЛ ЗАДАЧИ:\n" + json.dumps(cycle, ensure_ascii=False, indent=2)[:4000]
            result["payload"] = cycle
            if cycle.get("status") == "critical_change":
                result["ok"] = False
                result["message"] += "\nЗадача остановлена: нужен ответ человека."
        elif phase == "review":
            preflight_or_stop(state, cfg)
            task = state.next_task()
            if not task:
                result["message"] = "Нет задач для review."
                return result
            from .implementation import TaskEngine
            eng = TaskEngine(state, cfg, client)
            out = eng.run_review(task, on_event=on_event, request_approval=request_approval)
            result["message"] = "REVIEW:\n" + json.dumps(out, ensure_ascii=False, indent=2)[:2000]
            result["payload"] = out
        else:
            result["ok"] = False
            result["message"] = f"Фаза {phase} не обрабатывается автоматически."
    except PreflightError as e:
        result["ok"] = False
        result["message"] = str(e)
    except Exception as e:
        result["ok"] = False
        result["message"] = f"Ошибка: {type(e).__name__}: {e}"
    finally:
        asyncio.run(client.close())
    return result


def submit_answers(root: Path, answers: dict, config_path: str | None = None) -> dict:
    """Сохраняет ответы человека на pending-вопросы и продвигает фазу."""
    root = Path(root)
    state = ProjectState.load(root)
    if not state.pending_questions:
        return {"ok": False, "message": "Нет ожидающих вопросов."}
    # answers: {key: value}
    for q in state.pending_questions:
        key = str(q.get("key") or q.get("id") or "")
        if key and key in answers:
            state.answers[key] = answers[key]
            state.memory[f"question:{key}"] = answers[key]
    next_phase = state.pending_next_phase or state.phase
    state.phase = next_phase
    state.pending_questions = []
    state.pending_next_phase = ""
    state.save()
    return {"ok": True, "message": f"Ответы сохранены. Фаза: {next_phase}", "phase": next_phase}
