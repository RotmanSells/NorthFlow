from pathlib import Path
from northflow.memory import MemoryDB, NGramEmbedder

def test_store_and_recall(tmp_path: Path):
    db = MemoryDB(tmp_path / "mem.db")
    a = db.store_memory("Перешли на PostgreSQL ради простоты", kind="decision", tags=["db"])
    b = db.store_memory("Выбираем между Kafka и RabbitMQ", kind="decision")
    db.add_relation(a, b, "relates_to", "сравнение")
    res = db.recall("почему выбрали postgresql", top_k=3)
    assert res
    assert any("PostgreSQL" in r["content"] for r in res)
    # связь расширила выборку
    contents = " | ".join(r["content"] for r in res)
    assert "Kafka" in contents or "RabbitMQ" in contents
    db.close()

def test_episodes(tmp_path: Path):
    db = MemoryDB(tmp_path / "mem.db")
    eid = db.store_episode("developer", 1, "реализовал auth", requests=5, tokens=1000)
    db.add_episode_event(eid, "tool", "write_file src/auth.py")
    eps = db.list_episodes()
    assert eps and eps[0]["role"] == "developer"
    assert db.stats()["memories"] == 0
    assert db.stats()["episodes"] == 1
    db.close()

def test_forget(tmp_path: Path):
    db = MemoryDB(tmp_path / "mem.db")
    a = db.store_memory("old fact")
    b = db.store_memory("new fact")
    db.add_relation(a, b, "invalidated_by")
    db.forget(a)
    assert db.stats()["memories"] == 1
    assert db.related(b, limit=10) == []
    db.close()
