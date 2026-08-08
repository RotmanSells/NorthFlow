from pathlib import Path
from flowpilot.locks import WriterLock

def test_lock_exclusive(tmp_path: Path):
    a = WriterLock(tmp_path)
    b = WriterLock(tmp_path)
    assert a.acquire(1, 1)
    assert not b.acquire(2, 1)
    a.release()
    assert b.acquire(2, 1)
    b.release()
