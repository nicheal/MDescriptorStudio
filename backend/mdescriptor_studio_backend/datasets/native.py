"""ctypes binding for the native statistics geometry kernel (mds_native).

One call computes a frame's minimum interatomic distance and short-contact
flag — the per-frame hot loop behind the Overview statistics and the Data
Health panel (datasets/statistics.py).  The DLL is optional: when it is
missing (fresh checkouts, non-Windows CI) callers fall back to the scipy
reference implementation in ``statistics.py``, which stays the semantic
source of truth.  Built by ``scripts/build_native.ps1`` into
``datasets/_native/``; PyInstaller bundles that directory unchanged, so the
same relative lookup works in the frozen sidecar.
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

_LIBRARY_NAME = "mds_native.dll"
_RADII_TABLE_LEN = 97

_DOUBLE_P = ctypes.POINTER(ctypes.c_double)
_INT64_P = ctypes.POINTER(ctypes.c_int64)
_UINT8_P = ctypes.POINTER(ctypes.c_uint8)

_GEO_SIGNATURE = ctypes.CFUNCTYPE(
    ctypes.c_int,  # return code
    _DOUBLE_P,  # positions (n, 3)
    _INT64_P,  # numbers (n,)
    _DOUBLE_P,  # cell (3, 3) row-major
    _UINT8_P,  # pbc (3,)
    _DOUBLE_P,  # covalent radii table (97,)
    ctypes.c_int64,  # n
    ctypes.c_double,  # short-contact coefficient
    ctypes.c_double,  # cell determinant tolerance
    ctypes.c_int64,  # lattice-image stencil limit
    _DOUBLE_P,  # out: minimum distance (NaN => none)
    _UINT8_P,  # out: short-contact flag
)


def _library_candidates() -> list[Path]:
    candidates: list[Path] = []
    # package-relative: the dev tree and the PyInstaller bundle (which keeps
    # the package layout inside _MEIPASS) share this location
    candidates.append(Path(__file__).resolve().parent / "_native" / _LIBRARY_NAME)
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", "") or Path(sys.executable).parent)
        candidates.append(base / "_native" / _LIBRARY_NAME)
        candidates.append(Path(sys.executable).parent / _LIBRARY_NAME)
    return candidates


_LIBS: list[ctypes.CDLL] = []  # keep loaded libraries alive for the process


def _load() -> ctypes.CDLL | None:
    if os.environ.get("MDS_DISABLE_NATIVE"):
        return None
    for candidate in _library_candidates():
        if not candidate.is_file():
            continue
        try:
            lib = ctypes.CDLL(str(candidate))
            fn = _GEO_SIGNATURE(("mds_frame_geometry", lib))
            _LIBS.append(lib)
            return fn
        except (OSError, AttributeError, ValueError):
            continue
    return None


_FN = _load()


def native_available() -> bool:
    """True when the compiled kernel loaded (MDS_DISABLE_NATIVE forces False)."""
    return _FN is not None


def frame_geometry(
    positions,
    numbers,
    cell,
    pbc,
    radii_table,
    short_contact_coefficient: float,
    cell_det_tol: float,
    image_limit: int,
) -> tuple[float | None, bool] | None:
    """Native (min_distance, short_contact) for one frame.

    Returns None when the kernel is unavailable or reported an argument error
    (callers fall back to the scipy implementation); NaN positions and empty
    frames are valid inputs and yield (None, False) like the reference.
    """
    import numpy as np

    if _FN is None:
        return None
    pos = np.ascontiguousarray(positions, dtype=np.float64)
    if pos.ndim != 2 or pos.shape[1] != 3:
        return None
    num = np.ascontiguousarray(numbers, dtype=np.int64)
    if pos.shape[0] != num.size:
        # The C core reads 3 * num.size doubles out of pos; a mismatched pair
        # would read past the buffer. None means "use the scipy reference".
        return None
    if np.asarray(pbc).size != 3:
        return None
    cel = np.ascontiguousarray(cell, dtype=np.float64).reshape(9)
    pbc8 = np.ascontiguousarray(pbc, dtype=np.uint8)
    table = np.ascontiguousarray(radii_table, dtype=np.float64)
    if table.size != _RADII_TABLE_LEN:
        return None
    out_distance = ctypes.c_double(float("nan"))
    out_contact = ctypes.c_uint8(0)
    code = _FN(
        pos.ctypes.data_as(_DOUBLE_P),
        num.ctypes.data_as(_INT64_P),
        cel.ctypes.data_as(_DOUBLE_P),
        pbc8.ctypes.data_as(_UINT8_P),
        table.ctypes.data_as(_DOUBLE_P),
        num.size,
        float(short_contact_coefficient),
        float(cell_det_tol),
        int(image_limit),
        ctypes.byref(out_distance),
        ctypes.byref(out_contact),
    )
    if code != 0:
        return None
    distance = out_distance.value
    min_distance = None if distance != distance else distance  # NaN => none
    return min_distance, bool(out_contact.value)
