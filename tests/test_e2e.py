"""E2E: init → research (fake) → architecture (fake) → critic → roadmap → task → review → commit."""
import asyncio
import json
import subprocess
from pathlib import Path

from flowpilot.agent import AgentRun
from flowpilot.config import RuntimeConfig
from flowpilot.implementation import TaskEngine
from flowpilot.pipeline import make_client
from flowpilot.state import ProjectState, Stage, Task
from flowpilot.tools import ToolExecutor


class ScriptedClient:
    """Возвращает tool_calls из скрипта; в конце finish."""
    def __init__(self, plan: list[dict]):
        self.plan = plan
        self.i = 0
        self.calls = 0

    async def chat(self, messages, model=None, tools=None, temperature=0.3):
        self.calls += 1
        if self.i >= len(self.plan):
            return type("R", (), {
                "content": "done text",
                "tool_calls": None,
                "usage": {"total_tokens": 1},
            })()
        step = self.plan[self.i]
        self.i += 1
        return type("R", (), {
            "content": None,
            "tool_calls": [type("T", (), {"id": f"c{self.calls}", "name": step["tool"], "arguments": step.get("args", {})})()],
            "usage": {"total_tokens": 1},
        })()


def make_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "AGENTS.md").write_text("rules", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "00-idea.md").write_text("# Идея\nApp", encoding="utf-8")
    (tmp_path / ".flowpilot.json").write_text(json.dumps({"phase": "roadmap", "stages": [
        {"id": 1, "title": "Auth", "description": "", "status": "in_progress", "tasks": [
            {"id": 1, "title": "Login", "description": "make login", "status": "todo", "stage_id": 1, "files": [], "tests": [], "notes": "", "created_at": "", "completed_at": ""}
        ]}
    ], "current_stage": 1, "next_task_id": 2, "answers": {}, "memory": {}, "updated_at": ""}), encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    return tmp_path


def test_e2e_scope_and_commit(tmp_path: Path):
    root = make_repo(tmp_path)
    state = ProjectState.load(root)
    cfg = RuntimeConfig()
    client = ScriptedClient([
        {"tool": "write_file", "args": {"path": "src/auth.py", "content": "print('auth')"}},
        {"tool": "write_file", "args": {"path": "../evil.py", "content": "bad"}},
        {"tool": "finish", "args": {"result": '{"done": true, "files": ["src/auth.py"], "tests": []}'}},
    ])
    eng = TaskEngine(state, cfg, client)
    task = state.stages[0].tasks[0]
    out = eng.run_task(task, run_checks=False)
    assert (root / "src" / "auth.py").exists()
    assert not (root.parent / "evil.py").exists()
    assert "done" in out["result"]
    # lock released
    assert not (root / ".flowpilot.lock").exists()

    # commit
    msg = eng.commit_task(task)
    assert msg == "commit ok"
    log = subprocess.run(["git", "-C", str(root), "log", "--oneline", "-1"], capture_output=True, text=True).stdout
    assert "task 1" in log
