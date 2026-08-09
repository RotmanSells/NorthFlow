from pathlib import Path
from northflow.memory import MemoryDB

def test_delete_episode(tmp_path: Path):
    db = MemoryDB(tmp_path / "m.db")
    e1 = db.store_episode("dev", 1, "task1", requests=3)
    e2 = db.store_episode("dev", 2, "task2", requests=5)
    db.delete_episode(e1)
    eps = db.list_episodes()
    assert [e["id"] for e in eps] == [e2]
    assert db.stats()["episodes"] == 1

def test_prune_episodes(tmp_path: Path):
    db = MemoryDB(tmp_path / "m.db")
    for i in range(10):
        db.store_episode("dev", i, f"task {i}")
    removed = db.prune_episodes(keep_last=3)
    assert removed == 7
    assert db.stats()["episodes"] == 3

def test_recall_source_and_dedup(tmp_path: Path):
    db = MemoryDB(tmp_path / "m.db")
    a = db.store_memory("Перешли на PostgreSQL ради простоты", kind="decision")
    b = db.store_memory("Выбираем Kafka или RabbitMQ", kind="decision")
    c = db.store_memory("SQLite для лёгких данных", kind="fact")
    db.add_relation(a, b, "relates_to", "сравнение")
    db.add_relation(b, c, "derived_from", "альтернатива")
    res = db.recall("почему postgresql", top_k=2, expand_relations=2)
    ids = [r["id"] for r in res]
    assert len(ids) == len(set(ids))
    sources = {r.get("source") for r in res}
    assert "semantic" in sources
    assert any("related:" in s for s in sources)
    assert "score" in res[0]
    db.close()
