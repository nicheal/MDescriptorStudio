"""Cordero covalent radii (Å) for the non-physical-structure (short-contact)
health check: a frame is non-physical when any atom pair — periodic images
included — sits closer than a coefficient × (r_i + r_j).  Values follow
Cordero et al., Dalton Trans. 2008 and match ase.data.covalent_radii for
Z 1-96 (the same table NepTrainKit scans with), so thresholds agree with the
reference tool."""

import numpy as np

# indexed by atomic number; index 0 is a placeholder (Z starts at 1)
_RADII = (
    0.0,  # placeholder
    # H    He
    0.31, 0.28,
    # Li   Be   B    C    N    O    F    Ne
    1.28, 0.96, 0.84, 0.76, 0.71, 0.66, 0.57, 0.58,
    # Na   Mg   Al   Si   P    S    Cl   Ar
    1.66, 1.41, 1.21, 1.11, 1.07, 1.05, 1.02, 1.06,
    # K    Ca   Sc   Ti   V    Cr   Mn   Fe   Co   Ni
    2.03, 1.76, 1.70, 1.60, 1.53, 1.39, 1.39, 1.32, 1.26, 1.24,
    # Cu   Zn   Ga   Ge   As   Se   Br   Kr
    1.32, 1.22, 1.22, 1.20, 1.19, 1.20, 1.20, 1.16,
    # Rb   Sr   Y    Zr   Nb   Mo   Tc   Ru   Rh   Pd
    2.20, 1.95, 1.90, 1.75, 1.64, 1.54, 1.47, 1.46, 1.42, 1.39,
    # Ag   Cd   In   Sn   Sb   Te   I    Xe
    1.45, 1.44, 1.42, 1.39, 1.39, 1.38, 1.39, 1.40,
    # Cs   Ba   La   Ce   Pr   Nd   Pm   Sm   Eu   Gd
    2.44, 2.15, 2.07, 2.04, 2.03, 2.01, 1.99, 1.98, 1.98, 1.96,
    # Tb   Dy   Ho   Er   Tm   Yb   Lu   Hf   Ta   W
    1.94, 1.92, 1.92, 1.89, 1.90, 1.87, 1.87, 1.75, 1.70, 1.62,
    # Re   Os   Ir   Pt   Au   Hg   Tl   Pb   Bi   Po
    1.51, 1.44, 1.41, 1.36, 1.36, 1.32, 1.45, 1.46, 1.48, 1.40,
    # At   Rn   Fr   Ra   Ac   Th   Pa   U    Np   Pu
    1.50, 1.50, 2.60, 2.21, 2.15, 2.06, 2.00, 1.96, 1.90, 1.87,
    # Am   Cm
    1.80, 1.69,
)

_ARRAY = np.asarray(_RADII, dtype=np.float64)


def radii_for(numbers: np.ndarray) -> np.ndarray:
    """Per-atom covalent radii; out-of-range Z clamps to the table ends
    (nothing practical exists beyond Cm, and Z<=0 is a corrupt frame)."""
    z = np.clip(np.asarray(numbers, dtype=np.int64), 1, len(_RADII) - 1)
    return _ARRAY[z]
