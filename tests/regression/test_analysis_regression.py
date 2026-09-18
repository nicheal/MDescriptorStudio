"""Numerical regression tests: same descriptor matrix -> same result.

The fixtures deliberately keep the descriptor matrix as a checked-in artifact
(descriptor.npy) so a change in an algorithm's numerical output is visible as
a test failure instead of silently changing every downstream analysis.
"""

from __future__ import annotations

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


def _samples() -> StructureDescriptorMatrix:
    descriptor = np.load(FIXTURES / "descriptor.npy")
    return StructureDescriptorMatrix(
        descriptor,
        np.arange(descriptor.shape[0], dtype=np.int64),
        sample_ids=[f"frame:{i}" for i in range(descriptor.shape[0])],
    )


def test_si_extxyz_fixture_is_readable() -> None:
    reader = create_adapter(FIXTURES / "Si.xyz")
    assert reader.metadata().number_of_frames == 2
    frames = reader.read()
    assert [frame.index for frame in frames] == [0, 1]
    assert all(frame.numbers.tolist() == [14, 14] for frame in frames)
    assert frames[0].cell.shape == (3, 3)
    assert frames[0].energy is not None


def test_analysis_numerical_regression() -> None:
    expected = np.load(FIXTURES / "expected.npz")
    samples = _samples()

    pca_result = pca(samples, {"preprocess": "standardized"})
    np.testing.assert_allclose(pca_result["arrays"]["coords"], expected["pca_coords"], rtol=1e-12, atol=1e-12)
    np.testing.assert_allclose(pca_result["arrays"]["explained_variance"], expected["pca_explained"], rtol=1e-12, atol=1e-12)

    variance = feature_variance(samples, {"histogram_bins": 8, "distribution_sample_size": 10})
    for name, value in variance["arrays"].items():
        key = f"variance_{name}"
        if key not in expected:
            continue
        np.testing.assert_allclose(value, expected[key], rtol=1e-12, atol=1e-12)

    dimension = effective_dimension(samples, {"preprocess": "standardized"})
    for name, value in dimension["arrays"].items():
        key = f"dimension_{name}"
        if key not in expected:
            continue
        np.testing.assert_allclose(value, expected[key], rtol=1e-12, atol=1e-12)

    kernel_result = kernel(samples, {"kernel": "rbf", "max_samples": 16})
    np.testing.assert_allclose(kernel_result["arrays"]["kernel_matrix"], expected["kernel_matrix"], rtol=1e-12, atol=1e-12)

    random = sampling(samples, {"n_samples": 6, "seed": 17}, "random")
    np.testing.assert_array_equal(random["arrays"]["selected_indices"], expected["random_indices"])
    fps = sampling(samples, {"n_samples": 6, "seed": 17}, "fps")
    np.testing.assert_array_equal(fps["arrays"]["selected_indices"], expected["fps_indices"])

    coverage_result = coverage(samples, samples, {"max_samples": 16, "metric": "euclidean"})
    np.testing.assert_allclose(coverage_result["arrays"]["distances"], expected["coverage_distances"], rtol=1e-12, atol=1e-12)
