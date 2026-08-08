from northflow.planning import validate_plan
from northflow.state import ProjectState, Stage, Task

def test_validate_good_plan(tmp_path):
    p = ProjectState.load(tmp_path)
    s = Stage(id=1, title="Auth", description="Клиент может зарегистрироваться и войти.", status="in_progress")
    s.tasks.append(Task(id=1, title="Регистрация", description="Форма, API, БД, тесты.",
                        files=["src/auth/register.py"], tests=["tests/test_register.py"]))
    p.stages.append(s)
    p.current_stage = 1
    assert validate_plan(p) == []

def test_validate_missing_fields(tmp_path):
    p = ProjectState.load(tmp_path)
    s = Stage(id=1, title="", description="")
    s.tasks.append(Task(id=1, title="", description=""))
    p.stages.append(s)
    errs = validate_plan(p)
    assert any("нет названия" in e for e in errs)
    assert any("нет списка файлов" in e for e in errs)
    assert any("нет списка тестов" in e for e in errs)
