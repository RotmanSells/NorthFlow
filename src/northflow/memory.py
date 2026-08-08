"""Память проекта: SQLite + векторы + эпизодические записи + связи (граф).

MVP без внешних серверов:
- memories  — факты/решения с эмбеддингом (BLOB) и важностью;
- relations — связи между фактами (граф);
- episodes  — эпизодическая память: что делал агент, сколько запросов/токенов;
- recall    — гибрид: вектор + ключевые слова + расширение по связям.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

EMBEDDING_DIM = 256

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY,
    content TEXT NOT NULL,
    kind TEXT DEFAULT 'fact',
    tags TEXT DEFAULT '[]',
    importance REAL DEFAULT 1.0,
    source_role TEXT,
    created_at TEXT,
    access_count INTEGER DEFAULT 0,
    embedding BLOB
);
CREATE TABLE IF NOT EXISTS relations (
    id INTEGER PRIMARY KEY,
    from_id INTEGER NOT NULL,
    to_id INTEGER NOT NULL,
    rel_type TEXT NOT NULL,
    note TEXT DEFAULT '',
    created_at TEXT,
    FOREIGN KEY(from_id) REFERENCES memories(id),
    FOREIGN KEY(to_id) REFERENCES memories(id)
);
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY,
    role TEXT,
    task_id INTEGER,
    summary TEXT,
    requests INTEGER DEFAULT 0,
    tokens INTEGER DEFAULT 0,
    duration_ms INTEGER DEFAULT 0,
    result TEXT DEFAULT '',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS episode_events (
    id INTEGER PRIMARY KEY,
    episode_id INTEGER NOT NULL,
    event TEXT,
    detail TEXT,
    created_at TEXT,
    FOREIGN KEY(episode_id) REFERENCES episodes(id)
);
"""

REL_TYPES = {
    "relates_to", "leads_to", "prefers_over", "contradicts",
    "reinforces", "invalidated_by", "evolved_into", "derived_from", "part_of",
}


# ---------------------------------------------------------------- embedders

class Embedder:
    """Интерфейс эмбеддера."""

    def embed(self, text: str) -> list[float]:
        raise NotImplementedError


class NGramEmbedder(Embedder):
    """Локальный fallback без зависимостей: вектор из n-грамм.

    Не «понимает смысл», но даёт детерминированный вектор для гибридного поиска.
    """

    def __init__(self, dim: int = EMBEDDING_DIM):
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        norm = re.sub(r"\s+", " ", text.lower()).strip()
        for n in (2, 3, 4):
            for i in range(max(0, len(norm) - n + 1)):
                h = int(hashlib.md5(norm[i : i + n].encode("utf-8")).hexdigest(), 16) % self.dim
                vec[h] += 1.0
        return normalize(vec)


class OpenAIEmbedder(Embedder):
    """Настоящий семантический эмбеддер через OpenAI-совместимый API."""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: int = 60):
        import httpx
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._client = httpx.Client(timeout=timeout)

    def embed(self, text: str) -> list[float]:
        resp = self._client.post(
            f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "input": text},
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]

    def close(self) -> None:
        self._client.close()


def normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-zа-яё0-9_]+", text.lower()))


# ---------------------------------------------------------------- storage

class MemoryDB:
    """SQLite-хранилище памяти проекта."""

    def __init__(self, path: Path | str, embedder: Embedder | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(_SCHEMA)
        self.db.commit()
        self.embedder = embedder or NGramEmbedder()

    def close(self) -> None:
        self.db.close()

    # --- memories -----------------------------------------------------

    def store_memory(
        self,
        content: str,
        kind: str = "fact",
        tags: Iterable[str] = (),
        importance: float = 1.0,
        source_role: str = "",
    ) -> int:
        vec = self.embedder.embed(content)
        cur = self.db.execute(
            "INSERT INTO memories(content, kind, tags, importance, source_role, created_at, embedding) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                content.strip(),
                kind,
                json.dumps(list(tags), ensure_ascii=False),
                max(0.0, min(importance, 1.0)),
                source_role,
                now_iso(),
                json.dumps(vec),
            ),
        )
        self.db.commit()
        return cur.lastrowid

    def add_relation(self, from_id: int, to_id: int, rel_type: str, note: str = "") -> int:
        rel_type = rel_type if rel_type in REL_TYPES else "relates_to"
        cur = self.db.execute(
            "INSERT INTO relations(from_id, to_id, rel_type, note, created_at) VALUES (?, ?, ?, ?, ?)",
            (from_id, to_id, rel_type, note, now_iso()),
        )
        self.db.commit()
        return cur.lastrowid

    def recall(
        self,
        query: str,
        top_k: int = 5,
        expand_relations: int = 1,
    ) -> list[dict]:
        rows = self.db.execute("SELECT * FROM memories").fetchall()
        if not rows:
            return []
        q_vec = self.embedder.embed(query)
        q_tokens = tokenize(query)
        scored: list[tuple[float, sqlite3.Row]] = []
        for r in rows:
            vec = json.loads(r["embedding"] or "[]")
            if not vec:
                vec = self.embedder.embed(r["content"])
            vec_score = cosine(q_vec, vec)
            content_tokens = tokenize(r["content"])
            kw = len(q_tokens & content_tokens) / max(1, len(q_tokens))
            recency = 0.0
            try:
                created = datetime.fromisoformat(r["created_at"]).timestamp()
                age_days = max(0.0, (datetime.now(timezone.utc).timestamp() - created) / 86400)
                recency = math.exp(-age_days / 30.0)
            except (ValueError, TypeError):
                pass
            score = (
                vec_score * 0.55
                + kw * 0.30
                + float(r["importance"]) * 0.10
                + recency * 0.05
            )
            scored.append((score, r))

        scored.sort(key=lambda x: x[0], reverse=True)
        picked = scored[:top_k]
        result = []
        seen: set[int] = set()
        for score, r in picked:
            seen.add(r["id"])
            result.append(memory_to_dict(r, score))
            # расширение по связям
            if expand_relations > 0:
                related = self.related(r["id"], limit=expand_relations)
                for rel in related:
                    if rel["to_id"] not in seen:
                        seen.add(rel["to_id"])
                        result.append(rel)
        for _score, r in picked:
            self.db.execute("UPDATE memories SET access_count = access_count + 1 WHERE id = ?", (r["id"],))
        self.db.commit()
        return result[: top_k * (1 + expand_relations)]

    def related(self, memory_id: int, limit: int = 5) -> list[dict]:
        rows = self.db.execute(
            "SELECT r.from_id, r.to_id, r.rel_type, r.note, m.id, m.content, m.kind "
            "FROM relations r JOIN memories m ON m.id = r.to_id "
            "WHERE r.from_id = ? ORDER BY r.id DESC LIMIT ?",
            (memory_id, limit),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "to_id": row["to_id"],
                "content": row["content"],
                "kind": row["kind"],
                "rel_type": row["rel_type"],
                "note": row["note"],
                "score": None,
            }
            for row in rows
        ]

    def forget(self, memory_id: int) -> None:
        self.db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        self.db.execute("DELETE FROM relations WHERE from_id = ? OR to_id = ?", (memory_id, memory_id))
        self.db.commit()

    def list_memories(self, limit: int = 50) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM memories ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [memory_to_dict(r) for r in rows]

    # --- episodes -----------------------------------------------------

    def store_episode(
        self,
        role: str,
        task_id: int | None,
        summary: str,
        requests: int = 0,
        tokens: int = 0,
        duration_ms: int = 0,
        result: str = "",
    ) -> int:
        cur = self.db.execute(
            "INSERT INTO episodes(role, task_id, summary, requests, tokens, duration_ms, result, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (role, task_id, summary, requests, tokens, duration_ms, result[:500], now_iso()),
        )
        self.db.commit()
        return cur.lastrowid

    def add_episode_event(self, episode_id: int, event: str, detail: str = "") -> None:
        self.db.execute(
            "INSERT INTO episode_events(episode_id, event, detail, created_at) VALUES (?, ?, ?, ?)",
            (episode_id, event, detail[:1000], now_iso()),
        )
        self.db.commit()

    def list_episodes(self, limit: int = 20) -> list[dict]:
        rows = self.db.execute("SELECT * FROM episodes ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        m = self.db.execute("SELECT COUNT(*) AS c FROM memories").fetchone()["c"]
        r = self.db.execute("SELECT COUNT(*) AS c FROM relations").fetchone()["c"]
        e = self.db.execute("SELECT COUNT(*) AS c FROM episodes").fetchone()["c"]
        return {"memories": m, "relations": r, "episodes": e}


def memory_to_dict(row: sqlite3.Row, score: float | None = None) -> dict:
    return {
        "id": row["id"],
        "content": row["content"],
        "kind": row["kind"],
        "tags": json.loads(row["tags"] or "[]"),
        "importance": row["importance"],
        "source_role": row["source_role"],
        "created_at": row["created_at"],
        "access_count": row["access_count"],
        "score": score,
    }


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
