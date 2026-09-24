"""Cell shear: off-diagonal deformation applied to cell and positions alike."""

from __future__ import annotations

import numpy as np

from ..models import StructureCandidate
from .strain import _affine_strain


class CellShear:
    """Simple shear: A = I + E with off-diagonal entries ~ U(−max, +max).

    Shearing the cell without the positions would slide every atom's
    fractional coordinate; the shared affine map (see _affine_strain) keeps
    the structure's placement in the box intact while the box itself skews.
    """

    name = "cell_shear"

    def __init__(self, max_shear: float = 0.05) -> None:
        self.max_shear = float(max_shear)

    def apply(self, parent: StructureCandidate, rng: np.random.Generator, params: dict) -> StructureCandidate:
        max_shear = float(params.get("max_shear", self.max_shear))
        if not 0.0 < max_shear < 1.0:
            raise ValueError("max_shear must be in (0, 1)")
        deformation = np.eye(3)
        iu = np.triu_indices(3, k=1)
        il = np.tril_indices(3, k=-1)
        deformation[iu] = rng.uniform(-max_shear, max_shear, size=3)
        deformation[il] = rng.uniform(-max_shear, max_shear, size=3)
        shear_values = np.concatenate([deformation[iu], deformation[il]])
        return _affine_strain(
            parent,
            deformation,
            self.name,
            {"max_shear": max_shear, "shear": shear_values.tolist()},
        )
