from pathlib import Path
import asyncio
from flowpilot.tools import ToolExecutor

def test_scope_block(tmp_path: Path):
    ex = ToolExecutor(tmp_path, allowed_paths=["."])
    out = asyncio.run(ex.execute("write_file", {"path": "../x.txt", "content": "bad"}))
    assert "denied" in out
    assert not (tmp_path.parent / "x.txt").exists()
    asyncio.run(ex.close())

def test_allowed_write(tmp_path: Path):
    ex = ToolExecutor(tmp_path, allowed_paths=["."])
    out = asyncio.run(ex.execute("write_file", {"path": "ok.txt", "content": "hi"}))
    assert (tmp_path / "ok.txt").read_text() == "hi"
    asyncio.run(ex.close())

def test_shell_blocks_destructive(tmp_path: Path):
    ex = ToolExecutor(tmp_path, allowed_paths=["."])
    out = asyncio.run(ex.execute("shell_exec", {"command": "rm -rf /tmp/whatever"}))
    assert "blocked" in out
    asyncio.run(ex.close())
