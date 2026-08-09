import json
from pathlib import Path
from northflow.web import WebServer

def test_web_serves_page_and_state(tmp_path: Path):
    (tmp_path / "docs").mkdir(exist_ok=True)
    (tmp_path / "AGENTS.md").write_text("rules")
    server = WebServer(tmp_path, port=8766)
    server.start()
    import urllib.request
    try:
        html = urllib.request.urlopen(server.url() + "/").read().decode()
        assert "North" in html and "Следующий шаг" in html
        with urllib.request.urlopen(server.url() + "/api/state") as r:
            data = json.loads(r.read())
        assert data["phase"] == "idea"
    finally:
        server.stop()
