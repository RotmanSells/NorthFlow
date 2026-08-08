"""Реализация задачи: preflight → lock → developer → проверки → review → commit → state."""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from .agent import AgentRun
from .checks import PreflightError, preflight
from .config import RuntimeConfig
from .locks import WriterLock
from .roles import ROLE_PROMPTS
from .state import ProjectState, Task
from .tools import ToolExecutor

DEFAULT_CHECK_COMMANDS = [
    ("lint", "ruff check ."),
    ("format", "ruff format --check ."),
    ("test", "pytest -q"),
]


class TaskEngine:
    def __init__(self, state: ProjectState, cfg: RuntimeConfig, client):
        self.state = state
        self.cfg = cfg
        self.client = client
        self.root = state.root

    def run_task(self, task: Task, expected_branch: str = "main", run_checks: bool = True) -> dict:
        errs = preflight(self.root, expected_branch=expected_branch, allow_dirty=True)
        if errs and expected_branch:
            raise PreflightError("PREFLIGHT FAIL:\n" + "\n".join(errs))

        lock = WriterLock(self.root)
        if not lock.acquire(task.id, task.stage_id or 0):
            raise PreflightError("Writer lock held: another writer is active.")
        try:
            role = self.cfg.roles["developer"]
            tools = ToolExecutor(
                self.root,
                memory=self.state.memory,
                allowed_paths=role.allowed_paths,
                role="developer",
            )
            sys_prompt = ROLE_PROMPTS["developer"]["system"].format(project=self.state.name)
            user = (
                f"Задача {task.id}: {task.title}\n\n{task.description}\n\n"
                f"Разрешённые пути: {role.allowed_paths}\n"
                f"Файлы по плану: {', '.join(task.files) or '(не заданы)'}"
            )
            run = AgentRun(self.client, role, self.root, sys_prompt, user, tools)
            result = asyncio.run(run.run())
            meta = {"requests": run.requests, "tokens": run.total_tokens}

            checks = {}
            if run_checks:
                for name, cmd in DEFAULT_CHECK_COMMANDS:
                    checks[name] = self._run_check(name, cmd)

            return {"result": result, "meta": meta, "checks": checks}
        finally:
            lock.release()

    def _run_check(self, name: str, cmd: str) -> str:
        try:
            res = subprocess.run(
                cmd.split(), cwd=str(self.root), capture_output=True, text=True, timeout=120,
            )
            return f"exit={res.returncode}" + (("\n" + res.stdout[:3000]) if res.stdout else "") + (("\n" + res.stderr[:3000]) if res.stderr else "")
        except Exception as e:
            return f"check failed: {e}"

    def run_review(self, task: Task) -> dict:
        role = self.cfg.roles["reviewer"]
        tools = ToolExecutor(
            self.root,
            memory=self.state.memory,
            allowed_paths=role.allowed_paths,
            role="reviewer",
        )
        sys_prompt = ROLE_PROMPTS["reviewer"]["system"].format(project=self.state.name)
        user = f"Проверь задачу {task.id}: {task.title}\n\n{task.description}"
        run = AgentRun(self.client, role, self.root, sys_prompt, user, tools)
        result = asyncio.run(run.run())
        return {"result": result, "requests": run.requests, "tokens": run.total_tokens}

    def commit_task(self, task: Task) -> str:
        try:
            subprocess.run(["git", "-C", str(self.root), "add", "-A"], check=True, capture_output=True, timeout=30)
            res = subprocess.run(
                ["git", "-C", str(self.root), "commit", "-m", f"task {task.id}: {task.title}"],
                capture_output=True, text=True, timeout=30,
            )
            if res.returncode == 0:
                return "commit ok"
            if "nothing to commit" in res.stderr:
                return "nothing to commit"
            return f"commit failed: {res.stderr[:500]}"
        except Exception as e:
            return f"commit error: {e}"
