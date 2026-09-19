"""Adapter + statistics unit tests on generated fixtures."""

from pathlib import Path

import numpy as np
import pytest

from make_fixtures import write_deepmd, write_extxyz

from mdescriptor_studio_backend.datasets import (
    compute_fingerprint,
    compute_statistics,
    create_adapter,
    detect_format,
    exporters,
)
from mdescriptor_studio_backend.datasets.ghosts import periodic_boundary_ghosts
from mdescriptor_studio_backend.datasets.statistics import _int_hist
from mdescriptor_studio_backend.errors import AppError


def test_detect_and_scan_deepmd(tmp_path: Path) -> None:
    d = tmp_path / "d"
    write_deepmd(d, 5, 64, seed=3)
    assert detect_format(d) == "deepmd"
    a = create_adapter(d)
    meta = a.scan()
    assert meta.number_of_frames == 5
    assert meta.elements == ["As", "Ga"]
    assert meta.periodicity["fully_periodic"] is True
    assert len(a) == 5


def test_deepmd_frame_roundtrip(tmp_path: Path) -> None:
    d = tmp_path / "d"
    write_deepmd(d, 4, 16, seed=5)
    a = create_adapter(d)
    f = a.get_frame(2)
    assert f.numbers.shape == (16,)
    assert set(np.unique(f.numbers)) == {31, 33}
    assert f.positions.shape == (16, 3)
    assert f.energy is not None and f.forces is not None
    assert abs(float(np.linalg.det(f.cell)) - 9.97**3) < 1.0


def test_deepmd_unlabeled_and_nopbc(tmp_path: Path) -> None:
    """dpdata System path (no energy.npy) + `nopbc` marker (ADR-19 semantics)."""
    d = tmp_path / "d"
    write_deepmd(d, 3, 16, seed=21)
    for name in ("energy.npy", "force.npy", "virial.npy"):
        (d / "set.000" / name).unlink()
    a = create_adapter(d)
    meta = a.scan()
    assert meta.number_of_frames == 3
    f = a.get_frame(1)
    assert f.energy is None and f.forces is None and f.virial is None

    d2 = tmp_path / "gas"
    write_deepmd(d2, 2, 16, seed=22)
    (d2 / "set.000" / "box.npy").unlink()
    (d2 / "nopbc").write_text("", encoding="utf-8")
    a2 = create_adapter(d2)
    assert a2.scan().periodicity["isolated"] is True
    assert a2.get_frame(0).pbc.tolist() == [False, False, False]


def test_deepmd_missing_type_map_rejected(tmp_path: Path) -> None:
    """dpdata would fall back to artificial Type_N names; descriptors need real Z."""
    d = tmp_path / "d"
    write_deepmd(d, 2, 16, seed=23)
    (d / "type_map.raw").unlink()
    try:
        create_adapter(d)
        raise AssertionError("should have raised")
    except AppError as e:
        assert e.code == "INVALID_DATASET"
        assert "type_map.raw" in e.message


def test_deepmd_flat_root_layout_unsupported(tmp_path: Path) -> None:
    """The self-made flat coord.npy-at-root layout was retired with ADR-19."""
    d = tmp_path / "d"
    write_deepmd(d, 2, 16, seed=24)
    for p in (d / "set.000").glob("*.npy"):
        p.replace(d / p.name)
    try:
        detect_format(d)
        raise AssertionError("should have raised")
    except AppError as e:
        assert e.code == "UNSUPPORTED_FORMAT"


def test_extxyz_random_access(tmp_path: Path) -> None:
    p = tmp_path / "d.xyz"
    write_extxyz(p, 7, 16, seed=9)
    assert detect_format(p) == "extxyz"
    a = create_adapter(p)
    assert len(a) == 7
    # random order access must be consistent
    f5 = a.get_frame(5)
    f0 = a.get_frame(0)
    f5b = a.get_frame(5)
    assert f5.index == 5 and f0.index == 0
    assert np.allclose(f5.positions, f5b.positions)
    assert f5.energy is not None and f5.forces is not None


def test_fingerprint_changes_with_content(tmp_path: Path) -> None:
    p = tmp_path / "d.xyz"
    write_extxyz(p, 3, 8, seed=1)
    fp1 = compute_fingerprint(p, 3)
    fp2 = compute_fingerprint(p, 3)
    assert fp1 == fp2
    write_extxyz(p, 4, 8, seed=1)
    assert compute_fingerprint(p, 4) != fp1


def test_statistics(tmp_path: Path) -> None:
    p = tmp_path / "d.xyz"
    write_extxyz(p, 6, 64, seed=13)
    a = create_adapter(p)
    stats = compute_statistics(a)
    assert stats["structures"] == 6
    assert stats["atoms_total"] == 6 * 64
    symbols = {e["symbol"] for e in stats["elements"]}
    assert symbols == {"Ga", "As"}
    assert stats["properties"]["energy"]["per_structure"] is True
    assert stats["properties"]["forces"]["per_atom"] is True
    assert stats["properties"]["virial"]["per_structure"] is True
    for key in ("energy_per_atom", "force_magnitude", "max_force", "min_distance", "volume", "atoms_per_structure"):
        hist = stats[key]
        assert hist is not None and len(hist["counts"]) == 40
        assert sum(hist["counts"]) == (6 if key != "force_magnitude" else 6 * 64)
    assert stats["periodicity"]["fully_periodic"] is True


def test_element_atom_counts_keep_wide_integer_bins() -> None:
    hist = _int_hist([4, 64, 512])
    assert hist is not None
    assert len(hist["counts"]) == 509
    assert hist["edges"][0:2] == [3.5, 4.5]
    assert hist["counts"][0] == 1
    assert hist["edges"][60:62] == [63.5, 64.5]
    assert hist["counts"][60] == 1
    assert hist["counts"][-1] == 1


def test_min_distance_per_structure(tmp_path: Path) -> None:
    """min_distance is one value per structure and minimum-image aware."""
    lat = "5.0 0.0 0.0 0.0 5.0 0.0 0.0 0.0 5.0"
    header = 'Properties=species:S:1:pos:R:3 pbc="T T T"'
    p = tmp_path / "md.xyz"
    p.write_text(
        # in-cell separation 4.8 Å, but 0.2 Å across the x boundary
        f'2\nLattice="{lat}" {header}\nSi 0.1 0.0 0.0\nSi 4.9 0.0 0.0\n'
        # plain in-cell pair
        f'2\nLattice="{lat}" {header}\nSi 0.0 0.0 0.0\nSi 1.0 0.0 0.0\n'
        # isolated: direct distance only, no images
        '2\nProperties=species:S:1:pos:R:3 pbc="F F F"\nSi 0.0 0.0 0.0\nSi 3.0 0.0 0.0\n'
        # single atom: nearest self-image = lattice constant
        f'1\nLattice="{lat}" {header}\nSi 0.0 0.0 0.0\n',
        encoding="utf-8",
    )
    stats = compute_statistics(create_adapter(p))
    hist, summary = stats["min_distance"], stats["min_distance_summary"]
    assert hist is not None and sum(hist["counts"]) == 4
    assert abs(summary["min"] - 0.2) < 1e-6
    assert abs(summary["max"] - 5.0) < 1e-6


def test_declared_isolated_box_is_not_periodic(tmp_path: Path) -> None:
    """A Lattice with pbc="F F F" is a box around an isolated structure (ASE
    writes clusters this way). Inventing periodicity there folds the two halves
    of the box together: this pair reads as a 0.1 Å non-physical contact and
    grows ghost atoms bonded across the vacuum."""
    lat = "12.0 0.0 0.0 0.0 12.0 0.0 0.0 0.0 30.0"
    p = tmp_path / "box.xyz"
    p.write_text(
        f'2\nLattice="{lat}" Properties=species:S:1:pos:R:3 pbc="F F F" energy=-1.0\n'
        "H 0.0 0.0 0.0\nH 11.9 0.0 0.0\n",
        encoding="utf-8",
    )
    adapter = create_adapter(p)
    frame = adapter.get_frame(0)
    assert frame.pbc.tolist() == [False, False, False]
    assert np.allclose(frame.cell, 0.0)  # nothing to wrap by
    stats = compute_statistics(adapter)
    assert stats["min_distance_summary"]["min"] == pytest.approx(11.9)
    assert stats["periodicity"]["isolated"] is True
    assert stats["health"]["nonphysical_structures"] == 0
    assert stats["health"]["invalid_cell"] == 0
    assert periodic_boundary_ghosts(["H", "H"], frame.positions, frame.cell) == []


def test_mixed_periodicity_still_flattens_to_periodic(tmp_path: Path) -> None:
    """Pinned deliberately: a slab keeps computing as fully periodic. Per-axis
    fidelity would make descriptors that do not advertise mixed_periodicity
    fail instead, which is an open product decision (see the extxyz reader)."""
    p = tmp_path / "slab.xyz"
    p.write_text(
        '2\nLattice="3.0 0.0 0.0 0.0 3.0 0.0 0.0 0.0 15.0"'
        ' Properties=species:S:1:pos:R:3 pbc="T T F" energy=-1.0\n'
        "Si 0.0 0.0 0.0\nSi 0.0 0.0 14.9\n",
        encoding="utf-8",
    )
    adapter = create_adapter(p)
    assert adapter.scan().periodicity["flags"] == ["XY."]
    assert adapter.get_frame(0).pbc.tolist() == [True, True, True]


def test_unsupported_format(tmp_path: Path) -> None:
    try:
        detect_format(tmp_path / "nope.bin")
        raise AssertionError("should have raised")
    except AppError as e:
        assert e.code == "UNSUPPORTED_FORMAT"


def _assert_same_frame(expected, actual) -> None:
    assert np.array_equal(np.asarray(expected.numbers), np.asarray(actual.numbers))
    assert float(expected.energy) == pytest.approx(float(actual.energy), rel=1e-7)
    for name in ("positions", "cell", "forces", "virial"):
        left, right = getattr(expected, name), getattr(actual, name)
        if left is None or right is None:
            assert left is None and right is None, name
        else:
            np.testing.assert_allclose(np.ravel(left), np.ravel(right), rtol=1e-6, atol=1e-6)


def test_exporters_round_trip_through_the_readers(tmp_path: Path) -> None:
    """A written system must reload through our own readers with every label.

    The DeepMD frame-property files are named by dpdata (``virial.npy``,
    singular); an unrecognized name is silently skipped on load, so compare
    values instead of checking that files exist.
    """
    source = tmp_path / "source"
    write_deepmd(source, 3, 8, seed=5)
    adapter = create_adapter(source)
    frames = [adapter.get_frame(index) for index in range(len(adapter))]

    deepmd_copy = tmp_path / "deepmd_copy"
    assert exporters.write_deepmd(deepmd_copy, frames) == 3
    exported = create_adapter(deepmd_copy)
    assert len(exported) == 3
    for index in range(3):
        _assert_same_frame(frames[index], exported.get_frame(index))

    xyz_copy = tmp_path / "copy.xyz"
    assert exporters.write_extxyz(xyz_copy, frames) == 3
    exported = create_adapter(xyz_copy)
    assert len(exported) == 3
    for index in range(3):
        _assert_same_frame(frames[index], exported.get_frame(index))
