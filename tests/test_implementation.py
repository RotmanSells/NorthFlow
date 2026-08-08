import asyncio
from pathlib import Path

from flowpilot.config import RuntimeConfig, RoleConfig, RoleBudget
from flowpilot.implementation import TaskEngine
from flowpilot.state import ProjectState, Stage, Task


class FakeDoneClient:
    async def chat(self, messages, model=None, tools=None, temperature=0.3):
        return type("R", (), {
            "content": None,
            "tool_calls": [type("T", (), {"id": "1", "name": "finish", "arguments": {"result": '{"done": true, "files": []}'}})],
            "usage": {"total_tokens": 1},
        })()


def test_task_engine_locks_and_finish(tmp_path: Path):
    cfg = RuntimeConfig()
    (tmp_path / "AGENTS.md").write_text("rules")
    (tmp_path / "docs").mkdir()
    from flowpilot.checks import git_state
    state = ProjectState.load(tmp_path)
    stage = Stage(id=1, title="S", description="", status="in_progress")
    stage.tasks.append(Task(id=1, title="T", description="d"))
    state.stages.append(stage)
    state.current_stage = 1
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    eng = TaskEngine(state, cfg, FakeDoneClient())
    out = eng.run_task(state.stages[0].tasks[0], run_checks=False)
    assert "done" in out["result"]
    assert not (tmp_path / ".flowpilot.lock").exists()
