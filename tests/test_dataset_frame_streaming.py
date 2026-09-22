"""Frame IPC stays bounded when one structure is larger than the viewer cap."""

import numpy as np

from mdescriptor_studio_backend.datasets.base import DatasetFrame
from mdescriptor_studio_backend.services.dataset_frame_service import DatasetFrameService, MAX_INLINE_ATOMS


class _Adapter:
    def __init__(self, natoms: int) -> None:
        self.frame = DatasetFrame(
            numbers=np.full(natoms, 6, dtype=np.int64),
            positions=np.arange(natoms * 3, dtype=np.float64).reshape(natoms, 3),
            cell=np.zeros((3, 3), dtype=np.float64),
            pbc=np.zeros(3, dtype=bool),
        )

    def get_frame(self, index: int) -> DatasetFrame:
        self.frame.index = index
        return self.frame


class _Datasets:
    def __init__(self, adapter: _Adapter) -> None:
        self.adapter = adapter

    def row_or_raise(self, _dataset_id: object) -> dict:
        return {"id": "ds_large", "name": "large"}

    def adapter_for(self, _row: dict) -> _Adapter:
        return self.adapter


def test_large_frame_has_split_summary_geometry_and_atom_pages() -> None:
    service = DatasetFrameService(_Datasets(_Adapter(MAX_INLINE_ATOMS + 1)))

    summary = service.frame_summary({"id": "ds_large", "index": 0})
    assert summary["atom_total"] == MAX_INLINE_ATOMS + 1
    assert summary["atom_rows_complete"] is False
    assert "xyz" not in summary
    assert "atom_rows" not in summary

    geometry = service.frame_geometry({"id": "ds_large", "index": 0})
    assert geometry["geometry_complete"] is False
    assert geometry["geometry_atom_count"] == MAX_INLINE_ATOMS
    assert int(geometry["xyz"].splitlines()[0]) == MAX_INLINE_ATOMS

    page = service.frame_atoms({"id": "ds_large", "index": 0, "atom_offset": MAX_INLINE_ATOMS, "atom_limit": 10})
    assert page["atom_rows_complete"] is False
    assert [row["i"] for row in page["atom_rows"]] == [MAX_INLINE_ATOMS]

    legacy = service.frame({"id": "ds_large", "index": 0})
    assert len(legacy["atom_rows"]) == MAX_INLINE_ATOMS
    assert legacy["geometry_complete"] is False
