"""CLI NorthFlow: init / status / preflight / run / questions."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import click

from .checks import PreflightError, preflight
from .config import RuntimeConfig, load_config, save_config
from .human import ask_questions, questions_from_result
from .pipeline import cmd_step, ensure_commit, make_client, preflight_or_stop
from .providers import LLMClient
from .roles import extract_json
from .planning import validate_plan
from .memory import MemoryDB
from .state import ProjectState, Stage, Task


@click.group()
def cli():
    pass


@cli.command()
@click.argument("project_dir", default=".")
def init(project_dir: str):
    """Создать структуру NorthFlow в папке проекта."""
    root = Path(project_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / "docs").mkdir(exist_ok=True)
    (root / "stages").mkdir(exist_ok=True)
    idea = root / "docs" / "00-idea.md"
    if not idea.exists():
        idea.write_text("# Идея\n\nОпиши здесь, что хочешь создать.\n", encoding="utf-8")
    state = ProjectState.load(root)
    state.save()
    click.echo(f"NorthFlow инициализирован в {root}")


@cli.command()
@click.argument("project_dir", default=".")
def status(project_dir: str):
    """Показать состояние проекта."""
    state = ProjectState.load(Path(project_dir).resolve())
    click.echo(f"Проект: {state.name}")
    click.echo(f"Фаза: {state.phase}")
    for s in state.stages:
        click.echo(f"  Этап {s.id} [{s.status}]: {s.title} — {sum(1 for t in s.tasks if t.status=='done')}/{len(s.tasks)} задач")
    if not state.stages:
        click.echo("  Этапов пока нет.")


@cli.command()
@click.argument("project_dir", default=".")
@click.option("--expected-branch", default="main", help="Ветка для preflight.")
def preflight_cmd(project_dir: str, expected_branch: str):
    """Проверить preflight перед запуском агентов."""
    root = Path(project_dir).resolve()
    try:
        preflight(root, expected_branch=expected_branch)
        click.echo("PREFLIGHT PASS")
    except PreflightError as e:
        click.echo(str(e), err=True)
        sys.exit(1)


@cli.command()
@click.argument("project_dir", default=".")
@click.option("--config", default=None, help="Путь к конфигу.")
def run(project_dir: str, config: str | None):
    """Прогнать следующий шаг конвейера."""
    root = Path(project_dir).resolve()
    cfg = load_config(config)
    state = ProjectState.load(root)
    client = make_client(cfg)

    try:
        if state.phase == "idea":
            step = cmd_step(client, cfg, state, "researcher",
                "Изучи docs/00-idea.md, проведи исследование, задай вопросы человеку.")
            print("ИССЛЕДОВАНИЕ:", json.dumps(step["payload"], ensure_ascii=False, indent=2)[:2000])
            qs = questions_from_result(step["result"])
            if qs:
                ask_questions(state, qs)
            state.phase = "architecture"
            state.save()
        elif state.phase == "architecture":
            step = cmd_step(client, cfg, state, "architect",
                "Составь полную документацию и AGENTS.md. Если нужны решения — задай вопросы человеку.")
            print("АРХИТЕКТУРА:", json.dumps(step["payload"], ensure_ascii=False, indent=2)[:2000])
            qs = questions_from_result(step["result"])
            if qs:
                ask_questions(state, qs)
                step = cmd_step(client, cfg, state, "architect",
                    "Продолжи: с учётом ответов заверши документацию.")
                print("АРХИТЕКТУРА (финал):", json.dumps(step["payload"], ensure_ascii=False, indent=2)[:2000])
            crit = cmd_step(client, cfg, state, "critic",
                "Проверь созданную архитектуру, дай вердикт.")
            print("КРИТИК:", json.dumps(crit["payload"], ensure_ascii=False, indent=2)[:2000])
            state.phase = "roadmap"
            state.save()
        elif state.phase == "roadmap":
            step = cmd_step(client, cfg, state, "planner",
                "Составь этапы и 3-5 детальных задач для первого этапа.")
            print("ПЛАНИРОВЩИК:", json.dumps(step["payload"], ensure_ascii=False, indent=2)[:2000])
            errors = validate_plan(state)
            if errors:
                print("ПЛАН НЕ ПОЛНЫЙ:", "\n".join(errors[:20]))
                print("Сначала исправь план, потом снова northflow run.")
                return
            state.phase = "implementation"
            state.save()
        elif state.phase == "implementation":
            preflight_or_stop(state, cfg)
            task = state.next_task()
            if not task:
                print("Все задачи этапа завершены. Закрой этап вручную в state.")
                return
            from .implementation import TaskEngine
            eng = TaskEngine(state, cfg, client)
            cycle = eng.complete_task_cycle(task)
            print("ЦИКЛ ЗАДАЧИ:", json.dumps(cycle, ensure_ascii=False, indent=2)[:4000])
            if cycle.get("status") == "critical_change":
                print("Задача остановлена: нужен ответ человека.")
        elif state.phase == "review":
            preflight_or_stop(state, cfg)
            task = state.next_task()
            if not task:
                print("Нет задач для review.")
                return
            from .implementation import TaskEngine
            eng = TaskEngine(state, cfg, client)
            out = eng.run_review(task)
            print("REVIEW:", json.dumps(out, ensure_ascii=False, indent=2)[:2000])
        else:
            print(f"Фаза {state.phase} не обрабатывается автоматически.")
    finally:
        asyncio.run(client.close())


@cli.command()
@click.argument("project_dir", default=".")
@click.option("--config", default=None)
def questions(project_dir: str, config: str | None):
    """Показать/ввести ответы на вопросы."""
    root = Path(project_dir).resolve()
    state = ProjectState.load(root)
    click.echo(json.dumps(state.answers, ensure_ascii=False, indent=2) or "(пусто)")


@cli.command()
@click.argument("project_dir", default=".")
def roadmap(project_dir: str):
    """Перегенерировать roadmap.md."""
    from .state import write_roadmap
    state = ProjectState.load(Path(project_dir).resolve())
    write_roadmap(state)
    click.echo("roadmap.md обновлён")



@cli.group()
def memory():
    """Память проекта: лог операций агентов."""


@memory.command("log")
@click.argument("project_dir", default=".")
@click.option("--limit", default=50, help="Сколько записей показать.")
@click.option("--role", default="", help="Фильтр по роли.")
@click.option("--action", default="", help="Фильтр по действию: store/recall/relation.")
def memory_log_cmd(project_dir: str, limit: int, role: str, action: str):
    """Показать журнал обращений к памяти."""
    root = Path(project_dir).resolve()
    db = MemoryDB(root / "memory.db")
    try:
        rows = db.list_memory_log(limit=limit, role=role, action=action)
        if not rows:
            click.echo("Записей пока нет.")
            return
        for r in rows:
            preview = (r["query"] or "").replace("\n", " ")[:80]
            click.echo(f"#{r['id']} [{r['created_at']}] {r['role']} / {r['action']}: {preview}")
    finally:
        db.close()


@memory.command("show")
@click.argument("log_id", type=int)
@click.argument("project_dir", default=".")
def memory_show_cmd(log_id: int, project_dir: str):
    """Показать детали одной записи журнала."""
    root = Path(project_dir).resolve()
    db = MemoryDB(root / "memory.db")
    try:
        r = db.get_memory_log(log_id)
        if not r:
            click.echo(f"Запись #{log_id} не найдена.")
            return
        click.echo(f"# {r['id']} — {r['role']} / {r['action']}")
        click.echo(f"Время: {r['created_at']}")
        click.echo(f"Запрос: {r['query']}")
        click.echo(f"Параметры: {r['request_detail']}")
        click.echo("Ответ:")
        click.echo(r["response_detail"] or "(пусто)")
        click.echo(f"Memory IDs: {r['memory_ids']}")
    finally:
        db.close()


@memory.command("stats")
@click.argument("project_dir", default=".")
def memory_stats_cmd(project_dir: str):
    """Показать статистику памяти."""
    root = Path(project_dir).resolve()
    db = MemoryDB(root / "memory.db")
    try:
        click.echo(json.dumps(db.stats(), ensure_ascii=False, indent=2))
        log_count = db.db.execute("SELECT COUNT(*) AS c FROM memory_log").fetchone()["c"]
        click.echo(f"log_entries: {log_count}")
    finally:
        db.close()


@memory.command("dashboard")
@click.argument("project_dir", default=".")
@click.option("--output", default=None, help="Куда сохранить HTML.")
@click.option("--open", "open_browser", is_flag=True, help="Открыть в браузере.")
def memory_dashboard_cmd(project_dir: str, output: str | None, open_browser: bool):
    """Собрать веб-дашборд обращений к памяти."""
    from .dashboard import render_dashboard
    root = Path(project_dir).resolve()
    out = render_dashboard(root, output)
    click.echo(f"Дашборд: {out}")
    if open_browser:
        import webbrowser
        webbrowser.open(out.resolve().as_uri())


if __name__ == "__main__":
    cli()
