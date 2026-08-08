from pathlib import Path
import json
from northflow.human import questions_from_result, _render_question
from northflow.state import ProjectState

def test_questions_from_payload():
    text = json.dumps({"research": "x", "questions": [
        {"key": "stack", "question": "Какой стек?", "options": [{"value": "fastapi", "label": "FastAPI"}, {"value": "django", "label": "Django"}]}
    ]}, ensure_ascii=False)
    qs = questions_from_result(text)
    assert qs and qs[0]["key"] == "stack"

def test_render_options_normalizes_strings():
    text, key, options = _render_question({"question": "Выбор", "options": ["да", "нет"]}, 1)
    assert options == [{"value": "да", "label": "да"}, {"value": "нет", "label": "нет"}]
    text2, key2, options2 = _render_question({"question": "Выбор2"}, 2)
    assert len(options2) == 3
