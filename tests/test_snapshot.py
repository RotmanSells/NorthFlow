from pathlib import Path
from northflow.snapshot import snapshot_tree, diff_snapshots, save_report

def test_diff(tmp_path: Path):
    (tmp_path / "a.txt").write_text("hello")
    before = snapshot_tree(tmp_path)
    (tmp_path / "a.txt").write_text("hello world\nnew line")
    (tmp_path / "b.txt").write_text("new")
    after = snapshot_tree(tmp_path)
    d = diff_snapshots(before, after)
    assert "b.txt" in d["created"]
    assert "a.txt" in d["changed"]
    assert d["changed"]["a.txt"]["delta"] == 1
    assert d["deleted"] == {}

def test_save_report(tmp_path: Path):
    p = save_report(tmp_path, 1, {"created": {}}, {"role": "dev"})
    assert p.exists()
    assert "task-1-" in p.name
