"""Multi-set DeepMD layout (set.000/, set.001/ ...) — bug3 regression."""

from pathlib import Path

import numpy as np
import pytest

from make_fixtures import write_deepmd

from mdescriptor_studio_backend.datasets import create_adapter, detect_format

from conftest import BackendProcess, wait_job

REAL_DATASET = Path(r"D:\mlffkit\mlffkit\tests\dpdata-C50Cl1")


def _make_multi_set(root: Path) -> None:
    (root / "set.000").mkdir(parents=True)
    (root / "set.001").mkdir(parents=True)
    a = root.parent / "a"
    b = root.parent / "b"
    write_deepmd(a, 4, 16, seed=1)
    write_deepmd(b, 3, 16, seed=2)
    for dst, src in ((root / "set.000", a / "set.000"), (root / "set.001", b / "set.000")):
        for f in src.glob("*.npy"):
            f.replace(dst / f.name)
    (root / "type.raw").write_text((a / "type.raw").read_text(encoding="utf-8"), encoding="utf-8")
    (root / "type_map.raw").write_text((a / "type_map.raw").read_text(encoding="utf-8"), encoding="utf-8")


def test_multi_set_detection_and_frames(tmp_path: Path) -> None:
    root = tmp_path / "ds"
    _make_multi_set(root)
    assert detect_format(root) == "deepmd"
    a = create_adapter(root)
    assert len(a) == 7
    meta = a.scan()
    assert meta.number_of_frames == 7
    # frames across the set boundary resolve to the right set
    f2, f3, f6 = a.get_frame(2), a.get_frame(3), a.get_frame(6)
    assert f3.index == 3 and f6.index == 6
    assert not np.allclose(f2.positions, f3.positions)
    assert f3.energy is not None and f3.forces is not None


def test_real_deepmd_dataset(tmp_path: Path) -> None:
    """Smoke against the user's real dataset (skipped when not on this machine)."""
    if not REAL_DATASET.exists():
        pytest.skip("real dataset not present")
    bp = BackendProcess(tmp_path)
    try:
        assert bp.read_line()["event"] == "backend.ready"
        resp = bp.request(10, "dataset.register", {"path": str(REAL_DATASET), "name": "C50Cl1"})
        done = wait_job(bp, resp["result"]["job_id"], timeout=120)
        assert done["status"] == "COMPLETED", done
        meta = bp.request(11, "dataset.list")["result"][0]
        assert meta["number_of_frames"] == 256
        assert set(meta["elements"]) == {"C", "Cl"}
        fr = bp.request(12, "dataset.frame", {"id": meta["id"], "index": 128})
        assert fr["result"]["natoms"] == 51
        assert fr["result"]["energy_per_atom"] is not None
        stats = bp.request(13, "dataset.statistics", {"id": meta["id"]})["result"]["stats"]
        assert stats["structures"] == 256
    finally:
        assert bp.close() == 0
