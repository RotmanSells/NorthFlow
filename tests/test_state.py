from pathlib import Path
from northflow.state import ProjectState, Stage, Task, parse_task_blocks, write_roadmap

def test_state_roundtrip(tmp_path: Path):
    p = ProjectState.load(tmp_path)
    p.stages.append(Stage(id=1, title="Auth", description="login", status="in_progress"))
    p.stages[0].tasks.append(Task(id=1, title="Login", description="x", status="todo"))
    p.current_stage = 1
    p.next_task_id = 2
    p.save()
    q = ProjectState.load(tmp_path)
    assert q.stages[0].tasks[0].title == "Login"
    assert q.next_task() is not None
    assert q.next_task().id == 1

def test_parse_tasks():
    md = """# Stage
## Task 1
### Title: A
### Description: desc
- `a.py`
## Task 2
### Title: B
### Description: desc2
- `b.py`
"""
    tasks = parse_task_blocks(md, 1)
    assert len(tasks) == 2
    assert tasks[0].files == ["a.py"]
    assert tasks[1].title == "B"

def test_roadmap(tmp_path: Path):
    p = ProjectState.load(tmp_path)
    p.stages.append(Stage(id=1, title="Auth", description="d"))
    p.stages[0].tasks.append(Task(id=1, title="Login", description="x"))
    write_roadmap(p)
    assert "Этап 1" in (tmp_path / "roadmap.md").read_text()
