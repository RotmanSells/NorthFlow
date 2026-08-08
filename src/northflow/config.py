"""Конфигурация NorthFlow: провайдеры, роли, бюджеты, workspace, safety."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[3]
CONFIG_FILE = Path.home() / ".northflow" / "config.json"


@dataclass
class RoleBudget:
    max_requests: int
    max_seconds: int
    output_first_minutes: int | None = None
    max_total_tokens: int | None = None


@dataclass
class RoleConfig:
    name: str
    description: str
    model: str
    temperature: float = 0.4
    budget: RoleBudget = field(default_factory=lambda: RoleBudget(12, 300))
    tools: list[str] = field(default_factory=list)
    allowed_paths: list[str] = field(default_factory=lambda: ["."])


DEFAULT_ROLES = {
    "researcher": RoleConfig(
        name="researcher",
        description="Изучает идею и собирает внешний контекст",
        model="",
        budget=RoleBudget(max_requests=20, max_seconds=240, output_first_minutes=4),
        tools=["web_search", "web_fetch", "read_file", "list_directory", "search_files", "memory_recall"],
    ),
    "architect": RoleConfig(
        name="architect",
        description="Проектирует архитектуру и документацию",
        model="",
        budget=RoleBudget(max_requests=16, max_seconds=360, output_first_minutes=6),
        tools=["read_file", "list_directory", "search_files", "write_file", "append_file", "code_edit", "memory_recall"],
    ),
    "critic": RoleConfig(
        name="critic",
        description="Независимо критикует план архитектора",
        model="",
        budget=RoleBudget(max_requests=8, max_seconds=240, output_first_minutes=3),
        tools=["read_file", "list_directory", "search_files"],
    ),
    "planner": RoleConfig(
        name="planner",
        description="Разбивает проект на этапы и ближайшие задачи",
        model="",
        budget=RoleBudget(max_requests=12, max_seconds=300, output_first_minutes=4),
        tools=["read_file", "list_directory", "search_files", "write_file", "append_file", "code_edit", "memory_recall"],
    ),
    "developer": RoleConfig(
        name="developer",
        description="Реализует одну задачу вертикальным срезом",
        model="",
        budget=RoleBudget(max_requests=40, max_seconds=900, output_first_minutes=8),
        tools=["read_file", "list_directory", "search_files", "write_file", "append_file", "code_edit", "shell_exec", "memory_recall"],
    ),
    "reviewer": RoleConfig(
        name="reviewer",
        description="Проверяет реализацию задачи",
        model="",
        budget=RoleBudget(max_requests=12, max_seconds=300, output_first_minutes=4),
        tools=["read_file", "list_directory", "search_files", "shell_exec"],
    ),
}


@dataclass
class RuntimeConfig:
    provider: str = "openai"
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = ""
    temperature: float = 0.3
    max_requests_per_turn: int = 200
    max_seconds_per_turn: int = 1800
    request_timeout: int = 300
    workspace: str = ""
    restrict_workspace: bool = True
    blocked_commands: list[str] = field(default_factory=list)
    roles: dict[str, RoleConfig] = field(default_factory=lambda: dict(DEFAULT_ROLES))


def load_config(path: str | Path | None = None) -> RuntimeConfig:
    p = Path(path) if path else CONFIG_FILE
    if not p.exists():
        return RuntimeConfig()
    raw = json.loads(p.read_text(encoding="utf-8"))
    cfg = RuntimeConfig()
    for key in ("provider", "base_url", "api_key", "model", "temperature",
                "max_requests_per_turn", "max_seconds_per_turn", "request_timeout",
                "workspace", "restrict_workspace", "blocked_commands"):
        if key in raw:
            setattr(cfg, key, raw[key])
    roles = dict(DEFAULT_ROLES)
    for name, data in raw.get("roles", {}).items():
        if name in roles:
            roles[name].model = data.get("model", roles[name].model)
            roles[name].temperature = data.get("temperature", roles[name].temperature)
            b = data.get("budget", {})
            old = roles[name].budget
            roles[name].budget = RoleBudget(
                max_requests=b.get("max_requests", old.max_requests),
                max_seconds=b.get("max_seconds", old.max_seconds),
                output_first_minutes=b.get("output_first_minutes", old.output_first_minutes),
                max_total_tokens=b.get("max_total_tokens", old.max_total_tokens),
            )
            roles[name].tools = data.get("tools", roles[name].tools)
            roles[name].allowed_paths = data.get("allowed_paths", roles[name].allowed_paths)
    cfg.roles = roles
    return cfg


def save_config(cfg: RuntimeConfig, path: str | Path | None = None) -> None:
    p = Path(path) if path else CONFIG_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "provider": cfg.provider,
        "base_url": cfg.base_url,
        "api_key": cfg.api_key,
        "model": cfg.model,
        "temperature": cfg.temperature,
        "max_requests_per_turn": cfg.max_requests_per_turn,
        "max_seconds_per_turn": cfg.max_seconds_per_turn,
        "request_timeout": cfg.request_timeout,
        "workspace": cfg.workspace,
        "restrict_workspace": cfg.restrict_workspace,
        "blocked_commands": cfg.blocked_commands,
        "roles": {
            name: {
                "model": r.model,
                "temperature": r.temperature,
                "budget": {
                    "max_requests": r.budget.max_requests,
                    "max_seconds": r.budget.max_seconds,
                    "output_first_minutes": r.budget.output_first_minutes,
                    "max_total_tokens": r.budget.max_total_tokens,
                },
                "tools": r.tools,
                "allowed_paths": r.allowed_paths,
            }
            for name, r in cfg.roles.items()
        },
    }
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
