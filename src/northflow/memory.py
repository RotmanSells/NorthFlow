"""Память проекта: SQLite + векторы + эпизодические записи + связи (граф).

MVP без внешних серверов:
- memories  — факты/решения с эмбеддингом (BLOB) и важностью;
- relations — связи между фактами (граф);
- episodes  — эпизодическая память: что делал агент, сколько запросов/токенов;
- recall    — гибрид: вектор + ключевые слова + расширение по связям.

Производительность: по умолчанию гибридный recall сначала сужает кандидатов
по BM25-подобному ключевому скорингу (быстро, без индекса), потом считает
косинус только по кандидатам. Если доступен sqlite-vec loadable extension —
используется vec0 ANN-индекс. Всё остальное работает без внешних пакетов.
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
_CANDIDATE_SCAN_LIMIT = 400   # максимум кандидатов для полного векторного скоринга
_KW_CANDIDATE_LIMIT = 80      # минимум кандидатов по ключевым словам

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

CREATE TABLE IF NOT EXISTS memory_log (
    id INTEGER PRIMARY KEY,
    role TEXT,
    action TEXT,
    query TEXT,
    request_detail TEXT,
    response_detail TEXT,
    memory_ids TEXT DEFAULT '[]',
    created_at TEXT
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


def _bm25ish(content: str, query_tokens: set[str]) -> float:
    """Лёгкий ключевой скор без полнотекстового индекса: пересечение + плотность."""
    if not query_tokens:
        return 0.0
    tokens = tokenize(content)
    if not tokens:
        return 0.0
    overlap = len(query_tokens & tokens)
    if overlap == 0:
        return 0.0
    return overlap / math.sqrt(len(tokens))


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
        self.vec_loaded = self._try_load_sqlite_vec()
        self._last_role: str = ""

    def close(self) -> None:
        self.db.close()

    # --- optional ANN -------------------------------------------------

    def _try_load_sqlite_vec(self) -> bool:
        """Пытается загрузить sqlite-vec. Возвращает True, если ANN доступен."""
        try:
            import sqlite_vec
            self.db.enable_load_extension(True)
            sqlite_vec.load(self.db)
            self.db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memories_vec USING vec0(embedding float[256])")
            return True
        except Exception:
            return False

    def _ann_candidates(self, q_vec: list[float], top_k: int) -> list[dict]:
        if not self.vec_loaded:
            return []
        try:
            import sqlite_vec as _sv
            rows = self.db.execute(
                "SELECT rowid FROM memories_vec ORDER BY embedding LIMIT ?",
                (_sv.serialize_float32(q_vec), top_k * 4),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def _sync_vec_index(self) -> None:
        """Пересинхронизирует ANN-таблицу (полная перезапись, вызывается при store)."""
        if not self.vec_loaded:
            return
        try:
            self.db.execute("DELETE FROM memories_vec")
            for r in self.db.execute("SELECT id, embedding FROM memories").fetchall():
                vec = json.loads(r["embedding"] or "[]")
                if vec:
                    import sqlite_vec as _sv
                    self.db.execute(
                        "INSERT INTO memories_vec(rowid, embedding) VALUES (?, ?)",
                        (r["id"], _sv.serialize_float32(vec)),
                    )
            self.db.commit()
        except Exception:
            self.db.rollback()

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
        self._sync_vec_index()
        self.log_memory_op(
            role=source_role,
            action="store",
            query=content,
            request_detail=f"kind={kind}, tags={json.dumps(list(tags), ensure_ascii=False)}",
            response_detail=f"memory_id={cur.lastrowid}",
            memory_ids=[cur.lastrowid],
        )
        return cur.lastrowid

    def add_relation(self, from_id: int, to_id: int, rel_type: str, note: str = "") -> int:
        rel_type = rel_type if rel_type in REL_TYPES else "relates_to"
        cur = self.db.execute(
            "INSERT INTO relations(from_id, to_id, rel_type, note, created_at) VALUES (?, ?, ?, ?, ?)",
            (from_id, to_id, rel_type, note, now_iso()),
        )
        self.db.commit()
        self.log_memory_op(
            role=self._last_role or "system",
            action="relation",
            query=f"{from_id} -> {to_id}",
            request_detail=rel_type,
            response_detail=note,
        )
        return cur.lastrowid

    def recall(
        self,
        query: str,
        top_k: int = 5,
        expand_relations: int = 1,
    ) -> list[dict]:
        q_tokens = tokenize(query)
        q_vec = self.embedder.embed(query)

        # 1) Быстрые кандидаты: BM25-подобный скор, потом ANN, потом полный скан.
        rows = self.db.execute("SELECT * FROM memories").fetchall()
        kw_scored: list[tuple[float, sqlite3.Row]] = []
        for r in rows:
            s = _bm25ish(r["content"], q_tokens)
            if s > 0:
                kw_scored.append((s, r))
        kw_scored.sort(key=lambda x: x[0], reverse=True)
        kw_cands = [r for _s, r in kw_scored[: _KW_CANDIDATE_LIMIT]]

        ann_ids = {r["rowid"] for r in self._ann_candidates(q_vec, top_k)}
        cand_map = {r["id"]: r for r in rows}
        ann_rows = [cand_map[i] for i in ann_ids if i in cand_map]
        # Объединяем: ANN-кандидаты + ключевые кандидаты + (если мало) свежие.
        merged: list[sqlite3.Row] = []
        seen_ids: set[int] = set()
        for r in ann_rows + kw_cands:
            if r["id"] not in seen_ids:
                seen_ids.add(r["id"])
                merged.append(r)
        if len(merged) < _CANDIDATE_SCAN_LIMIT:
            # Полный скан только если кандидатов мало; иначе оставляем суженный набор.
            full = rows
            for r in full:
                if r["id"] not in seen_ids:
                    seen_ids.add(r["id"])
                    merged.append(r)
        candidates = merged[:_CANDIDATE_SCAN_LIMIT]

        # 2) Полный гибридный скор по кандидатам.
        scored: list[tuple[float, sqlite3.Row]] = []
        for r in candidates:
            vec = json.loads(r["embedding"] or "[]")
            if not vec:
                vec = self.embedder.embed(r["content"])
            vec_score = cosine(q_vec, vec)
            kw = len(q_tokens & tokenize(r["content"])) / max(1, len(q_tokens))
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
        result: list[dict] = []
        seen: set[int] = set()
        for score, r in picked:
            seen.add(r["id"])
            result.append(memory_to_dict(r, score, source="semantic"))
            if expand_relations > 0:
                for rel in self.related(r["id"], limit=expand_relations):
                    if rel["to_id"] not in seen:
                        seen.add(rel["to_id"])
                        result.append(rel)
        for _score, r in picked:
            self.db.execute("UPDATE memories SET access_count = access_count + 1 WHERE id = ?", (r["id"],))
        self.db.commit()
        final = result[: top_k * (1 + expand_relations)]
        self.log_memory_op(
            role=self._last_role or "system",
            action="recall",
            query=query,
            request_detail=f"top_k={top_k}, expand_relations={expand_relations}",
            response_detail=json.dumps(
                [{"id": m["id"], "content": m["content"][:200], "source": m.get("source")} for m in final],
                ensure_ascii=False,
            ),
            memory_ids=[m["id"] for m in final],
        )
        return final

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
                "source": f"related:{row['rel_type']}",
            }
            for row in rows
        ]

    def forget(self, memory_id: int) -> None:
        self.db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        self.db.execute("DELETE FROM relations WHERE from_id = ? OR to_id = ?", (memory_id, memory_id))
        self.db.commit()
        self._sync_vec_index()

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

    def delete_episode(self, episode_id: int) -> None:
        self.db.execute("DELETE FROM episode_events WHERE episode_id = ?", (episode_id,))
        self.db.execute("DELETE FROM episodes WHERE id = ?", (episode_id,))
        self.db.commit()

    def delete_episodes_before(self, before_iso: str) -> int:
        cur = self.db.execute(
            "SELECT id FROM episodes WHERE created_at < ?", (before_iso,)
        ).fetchall()
        ids = [r["id"] for r in cur]
        for eid in ids:
            self.db.execute("DELETE FROM episode_events WHERE episode_id = ?", (eid,))
        self.db.execute("DELETE FROM episodes WHERE created_at < ?", (before_iso,))
        self.db.commit()
        return len(ids)

    def prune_episodes(self, keep_last: int = 200) -> int:
        """Удаляет самые старые эпизоды, оставляя keep_last последних."""
        rows = self.db.execute("SELECT id FROM episodes ORDER BY id DESC LIMIT -1 OFFSET ?", (keep_last,)).fetchall()
        ids = [r["id"] for r in rows]
        for eid in ids:
            self.delete_episode(eid)
        return len(ids)

    def list_episodes(self, limit: int = 20) -> list[dict]:
        rows = self.db.execute("SELECT * FROM episodes ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        m = self.db.execute("SELECT COUNT(*) AS c FROM memories").fetchone()["c"]
        r = self.db.execute("SELECT COUNT(*) AS c FROM relations").fetchone()["c"]
        e = self.db.execute("SELECT COUNT(*) AS c FROM episodes").fetchone()["c"]
        return {"memories": m, "relations": r, "episodes": e}

    # --- memory log ---------------------------------------------------

    def log_memory_op(
        self,
        role: str,
        action: str,
        query: str = "",
        request_detail: str = "",
        response_detail: str = "",
        memory_ids: list[int] | None = None,
    ) -> int:
        cur = self.db.execute(
            "INSERT INTO memory_log(role, action, query, request_detail, response_detail, memory_ids, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                role or "system",
                action,
                query[:1000],
                request_detail[:2000],
                response_detail[:5000],
                json.dumps(memory_ids or [], ensure_ascii=False),
                now_iso(),
            ),
        )
        self.db.commit()
        return cur.lastrowid

    def list_memory_log(self, limit: int = 50, role: str = "", action: str = "") -> list[dict]:
        sql = "SELECT * FROM memory_log WHERE 1=1"
        params: list = []
        if role:
            sql += " AND role = ?"
            params.append(role)
        if action:
            sql += " AND action = ?"
            params.append(action)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = self.db.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_memory_log(self, log_id: int) -> dict | None:
        row = self.db.execute("SELECT * FROM memory_log WHERE id = ?", (log_id,)).fetchone()
        return dict(row) if row else None


def memory_to_dict(row: sqlite3.Row, score: float | None = None, source: str = "memory") -> dict:
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
        "source": source,
    }


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
