from pathlib import Path
import json
import time
from northflow.sse import StreamSession, sse_event

def test_sse_event_format():
    assert sse_event({"type": "phase", "data": {"name": "x"}}) == 'data: {"type": "phase", "data": {"name": "x"}}\n\n'

def test_stream_session_delivers_done_without_llm(tmp_path: Path):
    # Фаза идея без конфига/ключа не запустит LLM, но runner может упасть по preflight/ошибке —
    # главное проверить, что очередь получает done и None, не зависает.
    (tmp_path / "docs").mkdir(exist_ok=True)
    (tmp_path / "AGENTS.md").write_text("rules")
    session = StreamSession(tmp_path)
    session.start()
    got_done = False
    got_none = False
    for ev in session.events():
        if ev is None:
            got_none = True
            break
        if ev.get("type") == "done":
            got_done = True
    assert got_done
    assert got_none
