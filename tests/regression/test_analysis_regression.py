"""Numerical regression tests: same descriptor matrix -> same result.

The fixtures deliberately keep the descriptor matrix as a checked-in artifact
(descriptor.npy) so a change in an algorithm's numerical output is visible as
a test failure instead of silently changing every downstream analysis.

expected.npz pins every number those algorithms produce for that matrix.  It is
regenerated on purpose, never implicitly:

    MDS_REGENERATE_GOLDEN=1 python -m pytest tests/regression -k numerical
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from mdescriptor_studio_backend.analysis import StructureDescriptorMatrix
from mdescriptor_studio_backend.analysis.algorithms.kernel import kernel
from mdescriptor_studio_backend.analysis.algorithms.pairs import coverage
from mdescriptor_studio_backend.analysis.algorithms.pca import pca
from mdescriptor_studio_backend.analysis.metrics import effective_dimension, feature_variance
from mdescriptor_studio_backend.analysis.sampling.engine import sampling
from mdescriptor_studio_backend.datasets import create_adapter

FIXTURES = Path(__file__).resolve().parent
GOLDEN = FIXTURES / "expected.npz"


def _samples() -> StructureDescriptorMatrix:
    descriptor = np.load(FIXTURES / "descriptor.npy")
    return StructureDescriptorMatrix(
        descriptor,
        np.arange(descriptor.shape[0], dtype=np.int64),
        sample_ids=[f"frame:{i}" for i in range(descriptor.shape[0])],
    )


def _pinned(samples: StructureDescriptorMatrix) -> dict[str, np.ndarray]:
    """Every regression value, keyed the way expected.npz stores it."""
    values: dict[str, np.ndarray] = {}
    projection = pca(samples, {"preprocess": "standardized"})
    values["pca_coords"] = projection["arrays"]["coords"]
    values["pca_explained"] = projection["arrays"]["explained_variance"]
    variance = feature_variance(samples, {"histogram_bins": 8, "distribution_sample_size": 10})
    for name, value in variance["arrays"].items():
        values[f"variance_{name}"] = value
    dimension = effective_dimension(samples, {"preprocess": "standardized"})
    for name, value in dimension["arrays"].items():
        values[f"dimension_{name}"] = value
    values["kernel_matrix"] = kernel(samples, {"kernel": "rbf", "max_samples": 16})["arrays"]["kernel_matrix"]
    values["random_indices"] = sampling(samples, {"n_samples": 6, "seed": 17}, "random")["arrays"]["selected_indices"]
    values["fps_indices"] = sampling(samples, {"n_samples": 6, "seed": 17}, "fps")["arrays"]["selected_indices"]
    values["coverage_distances"] = coverage(
        samples, samples, {"max_samples": 16, "metric": "euclidean"}
    )["arrays"]["distances"]
    return values


def _sign_canonical(matrix: np.ndarray) -> np.ndarray:
    """Flip each PCA column so its largest-magnitude entry is positive."""
    flips = np.sign(matrix[np.abs(matrix).argmax(axis=0), np.arange(matrix.shape[1])])
    return matrix * np.where(flips == 0.0, 1.0, flips)


def test_si_extxyz_fixture_is_readable() -> None:
    reader = create_adapter(FIXTURES / "Si.xyz")
    assert reader.metadata().number_of_frames == 2
    frames = reader.read()
    assert [frame.index for frame in frames] == [0, 1]
    assert all(frame.numbers.tolist() == [14, 14] for frame in frames)
    assert frames[0].cell.shape == (3, 3)
    assert frames[0].energy is not None


def test_analysis_numerical_regression() -> None:
    samples = _samples()
    produced = _pinned(samples)
    if os.environ.get("MDS_REGENERATE_GOLDEN") == "1":
        np.savez(GOLDEN, **produced)
        return

    expected = np.load(GOLDEN)
    assert set(produced) == set(expected.files), "expected.npz no longer matches the algorithm outputs"
    for key, value in produced.items():
        reference = expected[key]
        if key == "pca_coords":
            # np.linalg.svd gives no sign convention, so only the unsigned
            # projection is reproducible across BLAS builds.
            value = _sign_canonical(value)
            reference = _sign_canonical(reference)
        if np.issubdtype(value.dtype, np.integer):
            np.testing.assert_array_equal(value, reference)
        else:
            np.testing.assert_allclose(value, reference, rtol=1e-12, atol=1e-12)

