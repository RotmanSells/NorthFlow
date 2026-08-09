import asyncio
from pathlib import Path
from northflow.approval import ApprovalManager, ApprovalWaiter
from northflow.tools import ToolExecutor
from northflow.checks import check_command

def test_check_command_blocks_destructive():
    assert check_command("rm -rf /tmp/x") is not None
    assert check_command("echo hello") is None

def test_approval_manager_resolves():
    m = ApprovalManager()
    token, q = m.request("echo hi")
    assert m.resolve(token, True)
    assert q.get(timeout=1) is True

def test_shell_normal_runs_without_approval(tmp_path: Path):
    ex = ToolExecutor(tmp_path, allowed_paths=["."])
    events = []
    async def waiter(command):
        events.append(command)
        return True
    ex.request_approval = waiter
    out = asyncio.run(ex.execute("shell_exec", {"command": "echo ok"}))
    assert events == []  # обычная команда не спрашивает
    assert "ok" in out
    asyncio.run(ex.close())

def test_shell_delete_asks_approval(tmp_path: Path):
    ex = ToolExecutor(tmp_path, allowed_paths=["."])
    events = []
    async def waiter(command):
        events.append(command)
        return True
    ex.request_approval = waiter
    (tmp_path / "x.txt").write_text("x")
    out = asyncio.run(ex.execute("shell_exec", {"command": "rm x.txt"}))
    assert events == ["rm x.txt"]
    assert not (tmp_path / "x.txt").exists()
    asyncio.run(ex.close())

def test_shell_delete_denied(tmp_path: Path):
    ex = ToolExecutor(tmp_path, allowed_paths=["."])
    async def waiter(command):
        return False
    ex.request_approval = waiter
    (tmp_path / "x.txt").write_text("x")
    out = asyncio.run(ex.execute("shell_exec", {"command": "rm x.txt"}))
    assert "отклонена" in out
    assert (tmp_path / "x.txt").exists()
    asyncio.run(ex.close())
