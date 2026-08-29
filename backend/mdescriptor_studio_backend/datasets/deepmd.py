"""DeepMD dataset adapter backed by the dpdata package (ADR-19).

The hand-written set.*/npy parser was retired; layout and label rules are
dpdata's `deepmd/npy` semantics: type.raw at the root plus set.*/coord.npy,
with box.npy (or a `nopbc` marker file) and optional energy/force/virial.npy.
Frames are concatenated in sorted set order; a dataset is labeled only when
energy.npy is present (dpdata raises DataError on LabeledSystem otherwise).
The whole system is loaded eagerly into memory at construction — the old
memmap lazy path is gone (documented trade-off of ADR-19).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ..errors import AppError, INVALID_DATASET, INVALID_PARAMS
from .base import DatasetAdapter, DatasetFrame, ScanMeta, pbc_summary
from .deepmd_symbols import _SYMBOL_TO_Z, _Z_TO_SYMBOL  # noqa: F401 (re-exported)


class DeepMDAdapter(DatasetAdapter):
    format_name = "deepmd"

    def __init__(self, source_path: Path):
        super().__init__(source_path)
        import dpdata  # deferred: heavy import, only needed for DeepMD datasets

        path = self.source_path
        sets = sorted(p for p in path.glob("set.*") if p.is_dir()) if path.is_dir() else []
        if not sets or not (path / "type.raw").exists():
            raise AppError(
                INVALID_DATASET,
                f"no DeepMD frames found (expected type.raw + set.*/coord.npy): {path}",
            )
        try:
            if any((s / "energy.npy").exists() for s in sets):
                system = dpdata.LabeledSystem(str(path), fmt="deepmd/npy")
            else:
                system = dpdata.System(str(path), fmt="deepmd/npy")
        except AppError:
            raise
        except Exception as exc:  # noqa: BLE001 - dpdata raises bare OS/Value errors
            raise AppError(
                INVALID_DATASET, f"invalid DeepMD dataset ({type(exc).__name__}): {exc}"
            ) from exc

        data = system.data
        symbols = list(data["atom_names"])
        z_by_type = [_SYMBOL_TO_Z.get(s) for s in symbols]
        missing = [s for s, z in zip(symbols, z_by_type) if z is None]
        if missing:
            # descriptors need real atomic numbers; dpdata would silently use
            # artificial "Type_N" names when type_map.raw is absent/unparseable
            raise AppError(
                INVALID_DATASET,
                f"type_map.raw is missing or holds non-element names {missing};"
                " a real element type map is required",
            )
        type_raw = np.asarray(data["atom_types"], dtype=np.int64).reshape(-1)
        self.natoms = int(type_raw.size)
        self.numbers = np.asarray([z_by_type[t] for t in type_raw], dtype=np.int64)

        self._coords = data["coords"]
        self._cells = data["cells"]
        self._energies = data.get("energies")
        self._forces = data.get("forces")
        self._virials = data.get("virials")

        if self._coords.ndim != 3 or self._coords.shape[1] != self.natoms:
            raise AppError(
                INVALID_DATASET,
                f"coord.npy holds {self._coords.shape[1] if self._coords.ndim == 3 else '?'}"
                f" atoms/frame but type.raw declares {self.natoms} atoms",
            )
        self.number_of_frames = int(self._coords.shape[0])

    # -- metadata -----------------------------------------------------------
    def scan(self) -> ScanMeta:
        file_size = sum(f.stat().st_size for f in self.source_path.rglob("*") if f.is_file())
        symbols = sorted({_Z_TO_SYMBOL[z] for z in self.numbers.tolist()})
        dets = np.abs(np.linalg.det(np.asarray(self._cells, dtype=np.float64))).reshape(-1)
        periodic_mask = dets > 1e-8
        if periodic_mask.all():
            pbc_set = {(True, True, True)}
        elif periodic_mask.any():
            pbc_set = {(True, True, True), (False, False, False)}
        else:
            pbc_set = {(False, False, False)}
        props = {
            "energy": self._energies is not None,
            "forces": self._forces is not None,
            "virial": self._virials is not None,
        }
        return ScanMeta(
            number_of_frames=self.number_of_frames,
            file_size=file_size,
            elements=symbols,
            properties=props,
            periodicity=pbc_summary(pbc_set),
        )

    def __len__(self) -> int:
        return self.number_of_frames

    def get_frame(self, index: int) -> DatasetFrame:
        if index < 0 or index >= self.number_of_frames:
            raise AppError(INVALID_PARAMS, f"frame index out of range: {index}")
        box = np.asarray(self._cells[index], dtype=np.float64).reshape(3, 3)
        periodic = abs(float(np.linalg.det(box))) > 1e-8
        energy = None
        if self._energies is not None:
            energy = float(np.asarray(self._energies[index]).reshape(-1)[0])
        forces = None
        if self._forces is not None:
            forces = np.asarray(self._forces[index], dtype=np.float64).reshape(self.natoms, 3)
        virial = None
        if self._virials is not None:
            virial = np.asarray(self._virials[index], dtype=np.float64).reshape(3, 3)
        return DatasetFrame(
            numbers=self.numbers,
            positions=np.asarray(self._coords[index], dtype=np.float64).reshape(self.natoms, 3),
            cell=box if periodic else np.zeros((3, 3)),
            pbc=np.array([periodic] * 3),
            energy=energy,
            forces=forces,
            virial=virial,
            index=index,
            id=f"frame_{index}",
        )
