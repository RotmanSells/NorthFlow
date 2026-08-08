import asyncio
from pathlib import Path

from flowpilot.agent import AgentRun
from flowpilot.config import RoleConfig, RoleBudget
from flowpilot.providers import LLMClient, ChatResponse, ToolCall


class FakeClient:
    def __init__(self):
        self.calls = 0

    async def chat(self, messages, model=None, tools=None, temperature=0.3):
        self.calls += 1
        return ChatResponse(content="final answer", usage={"total_tokens": 10})


class FakeLimitedClient:
    def __init__(self):
        self.calls = 0

    async def chat(self, messages, model=None, tools=None, temperature=0.3):
        self.calls += 1
        return ChatResponse(content="ok", tool_calls=[ToolCall(id="1", name="read_file", arguments={"path": "x"})], usage={"total_tokens": 1})


def test_agent_returns_final(tmp_path: Path):
    role = RoleConfig(name="test", description="", model="", budget=RoleBudget(5, 60), tools=["read_file"])
    (tmp_path / "x").write_text("data")
    run = AgentRun(FakeClient(), role, tmp_path, "sys", "user")
    out = asyncio.run(run.run())
    assert out == "final answer"
    assert run.requests == 1
    asyncio.run(run.close())

def test_agent_limit_returns_partial(tmp_path: Path):
    role = RoleConfig(name="test", description="", model="", budget=RoleBudget(2, 60), tools=["read_file"])
    (tmp_path / "x").write_text("data")
    run = AgentRun(FakeLimitedClient(), role, tmp_path, "sys", "user")
    out = asyncio.run(run.run())
    assert "PARTIAL" in out
    assert run.requests == 2
    asyncio.run(run.close())
