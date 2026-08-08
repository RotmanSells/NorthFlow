from pathlib import Path
import json
from datetime import datetime, timedelta, timezone
from northflow.locks import WriterLock

def test_stale_lock_released(tmp_path: Path):
    lock = WriterLock(tmp_path)
    lock.path.write_text(json.dumps({
        "task_id": 1, "stage_id": 1,
        "created_at": (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat(),
    }))
    assert lock.acquire(2, 1)
    assert lock.path.exists()

def test_fresh_lock_blocks(tmp_path: Path):
    lock = WriterLock(tmp_path)
    assert lock.acquire(1, 1)
    assert not lock.acquire(2, 1)
    lock.release()
    assert lock.acquire(2, 1)
