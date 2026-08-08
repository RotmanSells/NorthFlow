"""Инструменты агента с жёстким scope-контролем и critical_change."""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from pathlib import Path

import httpx

from .checks import check_command, is_allowed_write_path


def _tool(name: str, description: str, props: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": props, "required": required},
        },
    }


TOOL_DEFS = {
    "shell_exec": _tool(
        "shell_exec", "Выполнить команду в проекте. Запрещено: удаление, sudo, изменение прав системы.",
        {"command": {"type": "string"}}, ["command"]),
    "read_file": _tool("read_file", "Прочитать файл.", {"path": {"type": "string"}}, ["path"]),
    "write_file": _tool("write_file", "Создать или перезаписать файл (проверка пути).", {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
    "append_file": _tool("append_file", "Дописать в файл (проверка пути).", {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
    "code_edit": _tool("code_edit", "Найти и заменить фрагмент в файле (проверка пути).", {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, ["path", "old_text", "new_text"]),
    "list_directory": _tool("list_directory", "Список файлов в папке.", {"path": {"type": "string", "default": "."}}, []),
    "search_files": _tool("search_files", "Поиск текста в файлах.", {"query": {"type": "string"}, "path": {"type": "string", "default": "."}}, ["query"]),
    "web_search": _tool("web_search", "Поиск в интернете.", {"query": {"type": "string"}}, ["query"]),
    "web_fetch": _tool("web_fetch", "Открыть страницу по URL.", {"url": {"type": "string"}}, ["url"]),
    "memory_store": _tool("memory_store", "Сохранить факт в память проекта.", {"key": {"type": "string"}, "value": {"type": "string"}}, ["key", "value"]),
    "memory_recall": _tool("memory_recall", "Вспомнить факт из памяти проекта.", {"query": {"type": "string"}}, ["query"]),
    "finish": _tool("finish", "Завершить роль со structured-результатом в JSON.", {"result": {"type": "string"}}, ["result"]),
    "critical_change": _tool(
        "critical_change",
        "Остановиться и спросить человека, если задача противоречит документации/архитектуре.",
        {"question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}, "description": "Варианты ответа"}},
        ["question"],
    ),
}

WRITE_TOOLS = {"write_file", "append_file", "code_edit"}


class ToolError(Exception):
    pass


class ToolExecutor:
    def __init__(
        self,
        root: Path,
        memory: dict | None = None,
        allowed_paths: list[str] | None = None,
        role: str = "",
    ):
        self.root = root.resolve()
        self.memory = memory or {}
        self.allowed_paths = [Path(p).resolve() if Path(p).is_absolute() else (root / p).resolve()
                              for p in (allowed_paths or ["."])]
        self.role = role
        self._http = httpx.AsyncClient(timeout=30)
        self.events: list[dict] = []
        self.finish_payload: dict | None = None
        self.critical_change: dict | None = None

    async def close(self) -> None:
        await self._http.aclose()

    def get_defs(self, names: list[str] | None = None) -> list[dict]:
        if not names:
            return list(TOOL_DEFS.values())
        return [TOOL_DEFS[n] for n in names if n in TOOL_DEFS]

    def _resolve(self, raw: str) -> Path:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = self.root / p
        return p.resolve()

    def _check_write(self, path: str) -> Path:
        p = self._resolve(path)
        if not any(p.is_relative_to(a) for a in self.allowed_paths):
            raise ToolError(f"Путь запрещён: {path} вне разрешённых путей {[str(a) for a in self.allowed_paths]}")
        return p

    async def execute(self, name: str, args: dict) -> str:
        self.events.append({"tool": name, "args": args})
        try:
            if name == "shell_exec":
                return await self._shell(args.get("command", ""))
            if name == "read_file":
                return self._read(args["path"])
            if name in WRITE_TOOLS:
                return self._write(name, args)
            if name == "list_directory":
                return self._list(args.get("path", "."))
            if name == "search_files":
                return await self._search(args["query"], args.get("path", "."))
            if name == "web_search":
                return await self._web_search(args["query"])
            if name == "web_fetch":
                return await self._web_fetch(args["url"])
            if name == "memory_store":
                self.memory[args["key"]] = args["value"]
                return f"Сохранено в память: {args['key']}"
            if name == "memory_recall":
                q = args["query"].lower()
                hits = [f"{k}: {v}" for k, v in self.memory.items() if q in k.lower() or q in str(v).lower()]
                return "\n".join(hits[:10]) or "Совпадений в памяти нет."
            if name == "finish":
                try:
                    self.finish_payload = json.loads(args.get("result", "{}"))
                except json.JSONDecodeError:
                    self.finish_payload = {"result": args.get("result", "")}
                return "Роль завершена."
            if name == "critical_change":
                self.critical_change = {
                    "question": args.get("question", ""),
                    "options": args.get("options") or [],
                }
                return "КРИТИЧЕСКОЕ ИЗМЕНЕНИЕ: задача остановлена, ждём ответа человека."
            return f"Неизвестный инструмент: {name}"
        except ToolError as e:
            return f"Ошибка: {e}"
        except Exception as e:
            return f"Ошибка: {type(e).__name__}: {e}"

    async def _shell(self, command: str) -> str:
        bad = check_command(command)
        if bad:
            return f"Ошибка: команда запрещена политикой ({bad})."
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.root),
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=120)
        except asyncio.TimeoutError:
            proc.kill()
            return "Ошибка: команда превысила время ожидания."
        text = (out + (b"\n" + err if err else b"")).decode(errors="replace")
        return text[:50000] or "(нет вывода)"

    def _read(self, path: str) -> str:
        p = self._resolve(path)
        if not p.exists():
            return f"Ошибка: файл не найден: {p}"
        text = p.read_text(errors="replace")
        return text[:100000] + ("\n… (обрезано)" if len(text) > 100000 else "")

    def _write(self, name: str, args: dict) -> str:
        p = self._check_write(args["path"])
        if name == "write_file":
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(args.get("content", ""), encoding="utf-8")
            return f"Записан файл {p} ({len(args.get('content', ''))} байт)"
        if name == "append_file":
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(args.get("content", ""))
            return f"Дописан файл {p}"
        old = args.get("old_text", "")
        new = args.get("new_text", "")
        if not p.exists():
            return f"Ошибка: файл не найден: {p}"
        content = p.read_text(errors="replace")
        count = content.count(old)
        if count != 1:
            return f"Ошибка: old_text найден {count} раз (нужно ровно 1)."
        p.write_text(content.replace(old, new, 1), encoding="utf-8")
        return f"Изменён файл {p}."

    def _list(self, path: str) -> str:
        p = self._resolve(path)
        if not p.is_dir():
            return f"Ошибка: не папка: {p}"
        entries = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name))
        lines = []
        for e in entries[:300]:
            lines.append(("[папка] " if e.is_dir() else "[файл]  ") + e.name)
        return "\n".join(lines) or "(пусто)"

    async def _search(self, query: str, path: str) -> str:
        root = self._resolve(path)
        rg = shutil.which("rg")
        cmd = [rg, "--line-number", "--no-heading", "--hidden", "--glob", "!.git", query, str(root)] if rg \
            else ["grep", "-rn", query, str(root)]
        try:
            res = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                cwd=str(self.root),
            )
            out, err = await res.communicate()
        except Exception as e:
            return f"Ошибка: {e}"
        text = out.decode(errors="replace")
        if res.returncode not in (0, 1):
            return f"Ошибка: поиск не выполнен: {err.decode(errors='replace')[:500]}"
        return text[:100000].strip() or "Совпадений нет."

    async def _web_search(self, query: str) -> str:
        try:
            resp = await self._http.post(
                "https://lite.duckduckgo.com/lite/",
                data={"q": query},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=15,
            )
            if resp.status_code != 200:
                return f"Ошибка: поиск не выполнен, HTTP {resp.status_code}"
            anchors = re.findall(r"<a[^>]*class=['\"]result-link['\"][^>]*>(.*?)</a>", resp.text, re.DOTALL)
            links = re.findall(r"<a[^>]*href=['\"]([^'\"]+)['\"][^>]*class=['\"]result-link", resp.text)
            snips = re.findall(r"class=['\"]result-snippet['\"][^>]*>(.*?)</td>", resp.text, re.DOTALL)
            rows = []
            for i, (title, url) in enumerate(zip(anchors, links)):
                title = re.sub(r"<[^>]+>", "", title).strip()
                snip = re.sub(r"<[^>]+>", "", snips[i]).strip() if i < len(snips) else ""
                rows.append(f"**{title}**\n{url}\n{snip}")
            return "\n\n".join(rows[:8]) or "Результатов нет."
        except Exception as e:
            return f"Ошибка: поиск недоступен ({e})."

    async def _web_fetch(self, url: str) -> str:
        try:
            resp = await self._http.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20, follow_redirects=True)
            resp.raise_for_status()
            text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", resp.text, flags=re.DOTALL | re.I)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            return text[:80000]
        except Exception as e:
            return f"Ошибка: не удалось открыть страницу ({e})."
