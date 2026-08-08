"""CLI FlowPilot: init / status / questions / run / next."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import click

from .checks import PreflightError
from .config import RuntimeConfig, load_config, save_config
from .pipeline import (cmd_step, ensure_commit, make_client, preflight_or_stop,
                       run_role_prompt, write_roadmap)
from .providers import LLMClient
from .state import ProjectState, Stage, Task


@click.group()
def cli():
    pass


@cli.command()
@click.argument("project_dir", default=".")
def init(project_dir: str):
    """Создать структуру FlowPilot в папке проекта."""
    root = Path(project_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / "docs").mkdir(exist_ok=True)
    (root / "stages").mkdir(exist_ok=True)
    idea = root / "docs" / "00-idea.md"
    if not idea.exists():
        idea.write_text("# Идея\n\nОпиши здесь, что хочешь создать.\n", encoding="utf-8")
    state = ProjectState.load(root)
    state.save()
    click.echo(f"FlowPilot initialized in {root}")


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
    """Прогнать конвейер до текущей фазы."""
    root = Path(project_dir).resolve()
    cfg = load_config(config)
    state = ProjectState.load(root)
    client = make_client(cfg)

    try:
        if state.phase == "idea":
            step = cmd_step(client, cfg, state, "researcher",
                "Изучи docs/00-idea.md, проведи исследование, составь список вопросов человеку.")
            print("RESEARCH:", json.dumps(step["payload"], ensure_ascii=False, indent=2)[:2000])
            state.phase = "research"
            state.save()
        elif state.phase == "research":
            print("Фаза research: ответь на вопросы и укажи next phase через CLI (см. README).")
        elif state.phase == "architecture":
            step = cmd_step(client, cfg, state, "architect",
                "Составь полную документацию и AGENTS.md. Вопросы человеку — если критично.")
            print("ARCHITECT:", json.dumps(step["payload"], ensure_ascii=False, indent=2)[:2000])
            crit = cmd_step(client, cfg, state, "critic",
                "Проверь созданную архитектуру, дай вердикт.")
            print("CRITIC:", json.dumps(crit["payload"], ensure_ascii=False, indent=2)[:2000])
            state.phase = "roadmap"
            state.save()
        elif state.phase == "roadmap":
            step = cmd_step(client, cfg, state, "planner",
                "Составь этапы и 3-5 детальных задач для первого этапа.")
            print("PLANNER:", json.dumps(step["payload"], ensure_ascii=False, indent=2)[:2000])
            write_roadmap(state)
            state.save()
        elif state.phase == "implementation":
            preflight_or_stop(state, cfg)
            task = state.next_task()
            if not task:
                print("Все задачи завершены. Этап можно закрыть вручную.")
                return
            step = cmd_step(client, cfg, state, "developer",
                f"Реализуй задачу {task.id}: {task.title}\n{task.description}")
            print("DEV:", json.dumps(step["payload"], ensure_ascii=False, indent=2)[:2000])
            task.status = "done"
            task.completed_at = now_iso()
            state.save()
            msg = f"task {task.id}: {task.title}"
            print(ensure_commit(state.root, msg))
        elif state.phase == "review":
            preflight_or_stop(state, cfg)
            step = cmd_step(client, cfg, state, "reviewer",
                "Проверь последнюю реализацию. Верни вердикт.")
            print("REVIEW:", json.dumps(step["payload"], ensure_ascii=False, indent=2)[:2000])
        else:
            print(f"Фаза {state.phase} не обрабатывается автоматически.")
    finally:
        asyncio.run(client.close())


@cli.command()
@click.argument("project_dir", default=".")
@click.option("--config", default=None)
def questions(project_dir: str, config: str | None):
    """Показать/ввести ответы на вопросы (пока печатает research payload)."""
    root = Path(project_dir).resolve()
    state = ProjectState.load(root)
    click.echo(json.dumps(state.answers, ensure_ascii=False, indent=2) or "(пусто)")


@cli.command()
@click.argument("project_dir", default=".")
def roadmap(project_dir: str):
    """Перегенерировать roadmap.md."""
    state = ProjectState.load(Path(project_dir).resolve())
    write_roadmap(state)
    click.echo("roadmap.md updated")


if __name__ == "__main__":
    cli()
