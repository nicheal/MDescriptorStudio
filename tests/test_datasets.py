"""Adapter + statistics unit tests on generated fixtures."""

import tracemalloc
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
from mdescriptor_studio_backend.datasets.fingerprint import FINGERPRINT_VERSION
from mdescriptor_studio_backend.datasets.statistics import FORCE_MAGNITUDE_BIN
from mdescriptor_studio_backend.datasets.statistics import _ForceMagnitudeCounts
from mdescriptor_studio_backend.datasets.statistics import _hist
from mdescriptor_studio_backend.datasets.statistics import _int_hist
from mdescriptor_studio_backend.datasets.statistics import _summary
from mdescriptor_studio_backend.errors import AppError


def test_meta_skips_the_fingerprint_it_cannot_use(tmp_path: Path, monkeypatch) -> None:
    # A row stored before fingerprint versioning is answered entirely by the
    # legacy branch of DatasetService.meta: cache_valid is False and the status
    # is MIGRATING regardless. Measuring the versioned fingerprint anyway cost a
    # full directory walk plus a 32 MB sample on every read of such a dataset.
    from mdescriptor_studio_backend.services import dataset_service as ds_module
    from mdescriptor_studio_backend.services.dataset_service import DatasetService
    from mdescriptor_studio_backend.storage.database import Database

    calls: list[str] = []
    # The version prefix comes from the constant, not a literal: this test is
    # about versioned-versus-legacy spelling, and a hardcoded v3 reads as a
    # statement about the current version every time it is bumped.
    versioned = f"{FINGERPRINT_VERSION}:abc123"
    monkeypatch.setattr(
        ds_module,
        "compute_fingerprint",
        lambda source, frames, use_cache=True: calls.append("versioned") or versioned,
    )

    db = Database(tmp_path / "database.sqlite")
    # source_path is unique per dataset row, so the two generations get their own file
    for label, fingerprint in (("old", "fingerprint"), ("new", versioned)):
        source = tmp_path / f"{label}.extxyz"
        write_extxyz(source, n_frames=2, natoms=2)
        db.execute(
            "INSERT INTO datasets (id, name, format, source_path, number_of_frames, elements, properties,"
            " periodicity, fingerprint, file_size, created_at)"
            " VALUES (?, 'ds', 'extxyz', ?, 2, '[]', '{}', '{}', ?, 0, '2026-01-01T00:00:00+00:00')",
            (f"ds_{label}", str(source), fingerprint),
        )
    service = DatasetService(db, adapter=None, jobs=None, data_dir=tmp_path)

    legacy_meta = service.meta(service.row_or_raise("ds_old"))
    assert calls == [], "a pre-versioning row must not pay for the versioned walk"
    assert legacy_meta["fingerprint_status"] == "MIGRATING"
    assert legacy_meta["cache_valid"] is False

    assert service.meta(service.row_or_raise("ds_new"))["fingerprint_status"] == "CURRENT"
    assert calls == ["versioned"], "a versioned row must still verify its source"
    db.close()


def test_fingerprint_change_reason_names_the_upgrade_not_the_digests() -> None:
    # The sentence is written onto every run a change invalidates and is what a
    # hover on "STALE" shows, so it has to read as one line. A moved version
    # prefix means the reader changed, not the source, and the two digests are
    # outputs of different algorithms - nothing a user can compare by eye.
    from mdescriptor_studio_backend.services.dataset_service import fingerprint_change_reason

    upgraded = "fingerprint format upgraded ({old} -> v4); recompute descriptor results"
    assert fingerprint_change_reason("v3:aaaa", "v4:bbbb") == upgraded.format(old="v3")
    assert fingerprint_change_reason("fingerprint", "v4:bbbb") == upgraded.format(old="unversioned")
    assert fingerprint_change_reason("v4:aaaa", "v4:bbbb") == "source fingerprint changed (v4:aaaa -> v4:bbbb)"


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


def test_kept_force_magnitudes_reproduce_the_whole_array_statistics() -> None:
    """Under the memory budget the scan still reports what it always reported.

    A small dataset's median interpolates across the gap between its two middle
    values, which no count grid can recover, so exactness is what is promised
    here rather than a tolerance.
    """
    rng = np.random.default_rng(11)
    frames = [np.abs(rng.normal(0.4, 0.35, 300)) for _ in range(50)]
    counted = _ForceMagnitudeCounts()
    for frame in frames:
        counted.add(frame)
    assert counted.counts is None  # kept verbatim, not counted
    all_forces = np.concatenate(frames)
    assert counted.histogram() == _hist(all_forces)
    assert counted.summary() == _summary(all_forces)


def test_force_magnitudes_over_the_budget_are_counted_not_held() -> None:
    """Deep review D-8: every atom used to sit in a list until the scan ended,
    and the final `np.concatenate` asked for the whole pile again, all for a
    median. Past the budget the magnitudes are counted as they stream past."""
    rng = np.random.default_rng(5)
    frames = [np.abs(rng.normal(0.4, 0.35, 800)) for _ in range(2000)]  # 1.6M values
    reference_hist, reference_summary = _hist(np.concatenate(frames)), _summary(np.concatenate(frames))
    tracemalloc.start()
    counted = _ForceMagnitudeCounts()
    for frame in frames:
        counted.add(frame)
    histogram, summary = counted.histogram(), counted.summary()
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    # Those magnitudes are 12.8 MB of float64: held, and concatenated once, they
    # peak well above this bound.
    assert peak < 12 * 1024 * 1024
    assert counted.counts is not None  # the grid took over
    assert histogram["edges"] == reference_hist["edges"]  # the chart axis is exact
    assert sum(histogram["counts"]) == pytest.approx(sum(reference_hist["counts"]), abs=2)
    for key in ("min", "max", "mean"):
        assert summary[key] == reference_summary[key]
    assert abs(summary["median"] - reference_summary["median"]) <= FORCE_MAGNITUDE_BIN


def test_a_fingerprint_walk_slower_than_the_ttl_is_still_cached(tmp_path: Path, monkeypatch) -> None:
    """Pass 5, 5-B1: the entry was stamped with the time the call *started*, so a
    source whose walk outlasted the TTL went into the cache already expired and
    the next `dataset.list` walked the whole thing again - forever."""
    from types import SimpleNamespace

    from mdescriptor_studio_backend.datasets import fingerprint as fp

    source = tmp_path / "d.xyz"
    write_extxyz(source, 3, 8, seed=1)
    clock = {"now": 1000.0}
    monkeypatch.setattr(fp, "time", SimpleNamespace(monotonic=lambda: clock["now"]))
    real_files = fp._files

    def slow_walk(path):
        # One walk costs more than the whole cache lifetime.
        clock["now"] += fp.FINGERPRINT_CACHE_TTL_SECONDS + 1
        return real_files(path)

    monkeypatch.setattr(fp, "_files", slow_walk)
    first = fp.compute_fingerprint(source, 3)

    walks = 0

    def counting_walk(path):
        nonlocal walks
        walks += 1
        return real_files(path)

    monkeypatch.setattr(fp, "_files", counting_walk)
    assert fp.compute_fingerprint(source, 3) == first
    assert walks == 0, "a slow source was entered already expired, so nothing is ever cached"


def test_a_cold_dataset_is_loaded_once_under_concurrency(tmp_path: Path, monkeypatch) -> None:
    """Pass 5, 5-B2: `adapter_for` checked then wrote a plain dict, and three thread
    pools call it. Two first touches of a DeepMD set each read the whole source
    into memory and all but the last copy became unreachable."""
    import threading
    import time

    from mdescriptor_studio_backend.services.dataset_service import DatasetService

    source = tmp_path / "d.xyz"
    write_extxyz(source, 3, 8, seed=1)
    row = {"id": "ds_concurrent", "fingerprint": "fp", "format": "extxyz", "source_path": str(source)}

    class _Stub:
        def __init__(self) -> None:
            self._adapters: dict[str, object] = {}
            self._adapter_lock = threading.Lock()
            self.builds = 0

    stub = _Stub()

    def slow_create(path, detected):
        time.sleep(0.05)
        stub.builds += 1
        return object()

    monkeypatch.setattr("mdescriptor_studio_backend.services.dataset_service.create_adapter", slow_create)

    start = threading.Barrier(4)
    results: list[object] = []
    guard = threading.Lock()

    def load():
        start.wait(timeout=10)
        adapter = DatasetService.adapter_for(stub, row)
        with guard:
            results.append(adapter)

    threads = [threading.Thread(target=load) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert stub.builds == 1, f"four first touches built {stub.builds} copies of the same dataset"
    assert len({id(adapter) for adapter in results}) == 1


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


def test_mixed_periodicity_preserves_each_axis(tmp_path: Path) -> None:
    """A slab keeps its non-periodic vacuum direction throughout the reader."""
    p = tmp_path / "slab.xyz"
    p.write_text(
        '2\nLattice="3.0 0.0 0.0 0.0 3.0 0.0 0.0 0.0 15.0"'
        ' Properties=species:S:1:pos:R:3 pbc="T T F" energy=-1.0\n'
        "Si 0.0 0.0 0.0\nSi 0.0 0.0 14.9\n",
        encoding="utf-8",
    )
    adapter = create_adapter(p)
    assert adapter.scan().periodicity["flags"] == ["XY."]
    assert adapter.get_frame(0).pbc.tolist() == [True, True, False]


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
    assert exporters.write_deepmd(deepmd_copy, iter(frames)) == 3
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


def test_deepmd_reader_maps_source_arrays_instead_of_concatenating_them(tmp_path: Path) -> None:
    source = tmp_path / "source"
    write_deepmd(source, 4, 8, seed=6)
    adapter = create_adapter(source)
    assert all(isinstance(record["coords"], np.memmap) for record in adapter._sets)
    assert adapter.get_frame(3).positions.shape == (8, 3)


def test_deepmd_export_rejects_species_order_changes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    write_deepmd(source, 2, 4, seed=8)
    adapter = create_adapter(source)
    frames = [adapter.get_frame(index) for index in range(len(adapter))]
    frames[1].numbers = frames[1].numbers[::-1]
    with pytest.raises(AppError, match="same species and atom order"):
        exporters.write_deepmd(tmp_path / "bad", frames)



def test_element_histogram_pads_structures_that_lack_the_element(tmp_path: Path) -> None:
    """`element_atom_counts` holds one value per structure, so a frame without
    that element contributes a zero bin instead of being skipped. The running
    accumulation is a sparse value -> count map, which must keep that rule."""
    source = tmp_path / "mixed.xyz"
    source.write_text(
        '3\nProperties=species:S:1:pos:R:3\nGa 0 0 0\nGa 2 0 0\nGa 0 2 0\n'
        '3\nProperties=species:S:1:pos:R:3\nGa 0 0 0\nAs 2 0 0\nAs 0 2 0\n',
        encoding="utf-8",
    )

    stats = compute_statistics(create_adapter(source))

    arsenic = stats["element_atom_counts"]["As"]
    assert arsenic["edges"][0] == -0.5  # the zero bin exists
    assert sum(arsenic["counts"]) == 2  # both structures are counted
    assert arsenic["counts"][0] == 1 and max(arsenic["counts"]) == 1
