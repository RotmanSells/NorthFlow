"""Preflight и детерминированные проверки до/во время реализации."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

FORBIDDEN_PATTERNS = [
    r"\bany\b",
    r"@ts-ignore",
    r"\beval\s*\(",
    r"\bwith\s*\(",
    r"\basync\s+void\b",
    r"\bThread\.sleep\s*\(",
    r"\bJSON\.parse\s*\(",
]

DANGEROUS_COMMANDS = [
    r"\brm\s+-[^\s]*r[^\s]*f\b",
    r"\bsudo\b",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r":\(\)\s*\{",
]


class PreflightError(Exception):
    pass


def git_state(root: Path) -> dict:
    def _run(*args: str) -> tuple[int, str]:
        try:
            res = subprocess.run(
                ["git", "-C", str(root), *args],
                capture_output=True, text=True, timeout=10,
            )
            return res.returncode, res.stdout.strip()
        except FileNotFoundError:
            return -1, "git not found"

    branch = ""
    code, out = _run("branch", "--show-current")
    if code == 0:
        branch = out
    code, out = _run("status", "--porcelain")
    dirty = code == 0 and bool(out)
    return {"branch": branch, "dirty": dirty, "git_available": code == 0}


def preflight(root: Path, expected_branch: str = "", allow_dirty: bool = False) -> list[str]:
    errors = []
    if not (root / ".git").exists():
        errors.append("Не git-репозиторий: сначала создай git init.")
    g = git_state(root)
    if expected_branch and g.get("branch") != expected_branch:
        errors.append(
            f"Неверная ветка: ожидалось '{expected_branch}', сейчас '{g.get('branch', '?')}'."
        )
    if g.get("dirty") and not allow_dirty:
        errors.append("Дерево грязное: закоммить или stash изменения перед началом.")
    if not (root / "AGENTS.md").exists():
        errors.append("Нет AGENTS.md с правилами кода для проекта.")
    if not (root / "docs").is_dir():
        errors.append("Нет docs/ — сначала заверши фазу документации.")
    return errors


def check_scope(path: Path, allowed: list[Path]) -> bool:
    try:
        return any(path.resolve().is_relative_to(a.resolve()) for a in allowed)
    except Exception:
        return False


def scan_forbidden(text: str) -> list[str]:
    hits = []
    for pattern in FORBIDDEN_PATTERNS:
        if re.search(pattern, text):
            hits.append(pattern)
    return hits


def check_command(command: str) -> str | None:
    for pattern in DANGEROUS_COMMANDS:
        if re.search(pattern, command, re.IGNORECASE):
            return pattern
    return None


def is_allowed_write_path(rel_path: str, allowed_prefixes: list[str]) -> bool:
    p = Path(rel_path).as_posix()
    for prefix in allowed_prefixes:
        pref = prefix.replace("\\", "/")
        if p == pref or p.startswith(pref.rstrip("/") + "/"):
            return True
    return False
