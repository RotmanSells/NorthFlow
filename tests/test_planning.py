from pathlib import Path
from northflow.planning import import_plan, import_tasks_for_stage
from northflow.state import ProjectState, Stage

def test_import_plan(tmp_path: Path):
    (tmp_path / "stages.md").write_text("""# План

## Stage 1: Регистрация
Описание этапа 1.

### Task 1: Регистрация клиента
**Description:** форма + API + БД
- `src/auth/register.py`

## Stage 2: Админка
Описание этапа 2.
""", encoding="utf-8")
    p = ProjectState.load(tmp_path)
    n = import_plan(p, tmp_path / "stages.md")
    assert n == 2
    assert p.stages[0].title == "Регистрация"
    assert (tmp_path / "stages" / "01" / "README.md").exists()

def test_import_tasks(tmp_path: Path):
    p = ProjectState.load(tmp_path)
    stage = Stage(id=1, title="Auth", description="", status="in_progress")
    p.stages.append(stage)
    p.current_stage = 1
    md = """### Task 1: Login
**Description:** логин
- `src/auth/login.py`

### Task 2: Register
**Description:** регистрация
- `src/auth/register.py`
"""
    n = import_tasks_for_stage(p, 1, md)
    assert n == 2
    assert p.next_task_id == 3
