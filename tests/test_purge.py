from datetime import datetime

from hadir.face.service import purge_snapshots


def test_purge_old_snapshots(tmp_path):
    for day in ("2026/01/05", "2026/06/30", "2026/10/27"):
        d = tmp_path / day
        d.mkdir(parents=True)
        (d / "080000_1001.jpg").write_bytes(b"x")
    removed = purge_snapshots(tmp_path, 180, datetime(2026, 10, 28))
    assert removed == 1
    assert not (tmp_path / "2026/01/05").exists()
    assert (tmp_path / "2026/06/30").exists() and (tmp_path / "2026/10/27").exists()
    assert purge_snapshots(tmp_path, 0, datetime(2026, 10, 28)) == 0
