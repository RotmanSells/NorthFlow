"""Оркестрация конвейера: исследование → вопросы → архитектура → этапы → задачи → реализация."""
from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .checks import preflight, PreflightError
from .config import RuntimeConfig
from .state import ProjectState, Stage, Task, parse_task_blocks, write_roadmap
from .tools import ToolExecutor


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_role_prompt(
    client, cfg: RuntimeConfig, state: ProjectState, role_name: str, user_message: str,
) -> tuple[str, dict]:
    """Запускает роль и возвращает (итог, usage/журнал)."""
    role = cfg.roles[role_name]
    prompts = ROLE_PROMPTS[role_name]
    sys_prompt = prompts["system"].format(
        project=state.name,
        root=state.root,
        phase=state.phase,
    )
    tool_ctx = build_tool_context(state)
    full_user = user_message + "\n\n" + tool_ctx
    run = AgentRun(client, role, state.root, sys_prompt, full_user)
    try:
        result = asyncio.run(run.run())
    finally:
        asyncio.run(run.close())
    return result, {"requests": run.requests, "tokens": run.total_tokens, "log": run.log}


from .agent import AgentRun
from .providers import LLMClient


def make_client(cfg: RuntimeConfig) -> LLMClient:
    return LLMClient(cfg.base_url, cfg.api_key, cfg.model, timeout=cfg.request_timeout)


ROLE_PROMPTS = {
    "researcher": {
        "system": (
            "Ты — исследователь проекта {project}.\n"
            "Изучай идею, ищи информацию в интернете, формируй выводы.\n"
            "Обязательно задавай человеку вопросы, если данных не хватает.\n"
            "Не пиши код. Работай с инструментами.\n"
            "Заверши роль вызовом finish с JSON: {{\"research\": ..., \"questions\": [...], \"recommended_stack\": ...}}"
        ),
    },
    "architect": {
        "system": (
            "Ты — архитектор проекта {project}.\n"
            "На основе идеи, ответов человека и исследования создай документацию:\n"
            "docs/01-idea.md, docs/02-research.md, docs/03-architecture.md, docs/04-security.md, docs/05-api.md, docs/06-adr.md, AGENTS.md\n"
            "Работай вертикальными срезами, Lego-модулями, TDD где уместно.\n"
            "Заверши finish: {{\"architecture_done\": true, \"documents\": [...]}}"
        ),
    },
    "critic": {
        "system": (
            "Ты — независимый критик проекта {project}.\n"
            "Прочитай docs/ и AGENTS.md, найди слабые места, противоречия, риски.\n"
            "Не редактируй файлы. Заверши finish: {{\"verdict\": \"approve\"|\"changes\", \"issues\": [...], \"suggestions\": [...]}}"
        ),
    },
    "planner": {
        "system": (
            "Ты — планировщик проекта {project}.\n"
            "Разбей проект на логичные этапы (вертикальные срезы).\n"
            "Каждый этап — закрытый пользовательский сценарий.\n"
            "Для текущего этапа распиши детально 3-5 задач по 2-3к строк кода каждая.\n"
            "Создай папку stages/0X/ с MD-файлом этапа и задачами.\n"
            "Заверши finish: {{\"stages\": [...], \"current_stage\": N}}"
        ),
    },
    "developer": {
        "system": (
            "Ты — разработчик проекта {project}.\n"
            "Выполни ТОЛЬКО текущую задачу вертикальным срезом.\n"
            "Читай AGENTS.md и документацию. Не нарушай запреты.\n"
            "После реализации запусти проверки (линтер/тесты) через shell_exec.\n"
            "Если задача противоречит архитектуре — остановись и задай вопрос с пометкой [КРИТИЧЕСКОЕ ИЗМЕНЕНИЕ].\n"
            "Заверши finish: {{\"done\": true, \"files\": [...], \"tests\": [...], \"notes\": \"...\"}}"
        ),
    },
    "reviewer": {
        "system": (
            "Ты — ревьюер проекта {project}.\n"
            "Проверь реализацию задачи: качество, безопасность, соответствие документации и DoD.\n"
            "Не меняй код без необходимости; если нашёл критичные проблемы — верни отчёт.\n"
            "Заверши finish: {{\"verdict\": \"approve\"|\"fixes\", \"issues\": [...], \"summary\": \"...\"}}"
        ),
    },
}


def build_tool_context(state: ProjectState) -> str:
    ctx = [f"Проект: {state.name}", f"Фаза: {state.phase}"]
    cur = state.current_stage_obj()
    if cur:
        ctx.append(f"Текущий этап: {cur.id} — {cur.title} ({cur.status})")
        for t in cur.tasks:
            ctx.append(f"  Задача {t.id}: [{t.status}] {t.title}")
    if state.answers:
        ctx.append("Ответы человека: " + json.dumps(state.answers, ensure_ascii=False))
    if state.memory:
        ctx.append("Память проекта: " + json.dumps(state.memory, ensure_ascii=False))
    return "\n".join(ctx)


def preflight_or_stop(state: ProjectState, cfg: RuntimeConfig) -> None:
    errors = preflight(state.root)
    if errors:
        raise PreflightError("PREFLIGHT FAIL:\n" + "\n".join(errors))
    if cfg.restrict_workspace and state.phase in ("implementation", "review", "done"):
        # Жёсткая проверка branch/dirty перед спаном разработчика.
        from .checks import git_state
        g = git_state(state.root)
        if g.get("dirty"):
            raise PreflightError("PREFLIGHT FAIL: dirty tree. Commit/stash before spawning developer.")


def extract_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def cmd_step(client, cfg, state, role, message) -> dict:
    result, meta = run_role_prompt(client, cfg, state, role, message)
    payload = extract_json(result) or {"raw": result}
    return {"role": role, "result": result, "payload": payload, "meta": meta}


def ensure_commit(root: Path, message: str) -> str:
    try:
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True, timeout=30)
        res = subprocess.run(["git", "-C", str(root), "commit", "-m", message], capture_output=True, text=True, timeout=30)
        if res.returncode == 0:
            return "commit ok"
        if "nothing to commit" in res.stderr:
            return "nothing to commit"
        return f"commit failed: {res.stderr[:500]}"
    except Exception as e:
        return f"commit error: {e}"
