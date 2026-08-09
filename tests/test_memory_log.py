from pathlib import Path
from northflow.memory import MemoryDB

def test_memory_log_auto_records(tmp_path: Path):
    db = MemoryDB(tmp_path / "m.db")
    mid = db.store_memory("PostgreSQL ради простоты", kind="decision", source_role="architect")
    db._last_role = "architect"
    res = db.recall("почему postgresql", top_k=3)
    db.add_relation(mid, mid, "relates_to")
    rows = db.list_memory_log()
    actions = [r["action"] for r in rows]
    assert "store" in actions
    assert "recall" in actions
    assert "relation" in actions
    assert any(r["role"] == "architect" for r in rows)
    detail = db.get_memory_log(rows[1]["id"])
    assert detail is not None
    assert "почему postgresql" in detail["query"]
    assert "PostgreSQL" in detail["response_detail"]
    db.close()

def test_memory_log_filters(tmp_path: Path):
    db = MemoryDB(tmp_path / "m.db")
    db.store_memory("fact a", source_role="researcher")
    db._last_role = "developer"
    db.recall("fact")
    only_store = db.list_memory_log(action="store")
    assert len(only_store) == 1 and only_store[0]["action"] == "store"
    only_dev = db.list_memory_log(role="developer")
    assert len(only_dev) == 1 and only_dev[0]["role"] == "developer"
    db.close()
