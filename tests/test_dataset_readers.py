"""Dataset reader interface and registry tests."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from mdescriptor_studio_backend.datasets import create_adapter
from mdescriptor_studio_backend.datasets.readers import create_reader, reader_formats
from mdescriptor_studio_backend.datasets.readers import registry as reader_registry
from mdescriptor_studio_backend.errors import AppError, INVALID_DATASET


DATA = Path(__file__).resolve().parent / "data" / "extxyz_small.xyz"

_FRAME = "2\nLattice=\"10 0 0 0 10 0 0 0 10\" Properties=species:S:1:pos:R:3\nGa {x} 0 0\nAs {x} 1 1\n"


def test_extxyz_reads_valid_frames_and_trailing_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "two_frames.xyz"
    path.write_text(_FRAME.format(x=0.0) + _FRAME.format(x=0.5) + "\n\n", encoding="utf-8")
    assert len(create_adapter(path)) == 2


def test_extxyz_surplus_atom_line_fails_instead_of_dropping_frames(tmp_path: Path) -> None:
    # Frame 1 declares 2 atoms but supplies 3. The surplus line is then read as
    # the next frame's atom count; stopping there (the old behaviour) persisted a
    # frame count plus fingerprint describing only part of the file.
    path = tmp_path / "desync.xyz"
    path.write_text(
        _FRAME.format(x=0.0).rstrip("\n") + "\nGa 9 9 9\n" + _FRAME.format(x=0.5),
        encoding="utf-8",
    )
    with pytest.raises(AppError) as exc:
        create_adapter(path)
    assert exc.value.code == INVALID_DATASET


def test_extxyz_frame_without_header_line_fails(tmp_path: Path) -> None:
    path = tmp_path / "truncated.xyz"
    path.write_text("2\n", encoding="utf-8")
    with pytest.raises(AppError) as exc:
        create_adapter(path)
    assert exc.value.code == INVALID_DATASET


def test_extxyz_non_finite_coordinate_fails_the_frame_read(tmp_path: Path) -> None:
    # NaN encodes as a bare JSON token the renderer's JSON.parse rejects, so a
    # frame carrying one would hang the request instead of answering it.
    path = tmp_path / "nan.xyz"
    path.write_text(
        '2\nLattice="10 0 0 0 10 0 0 0 10" Properties=species:S:1:pos:R:3\n'
        "Ga 0 0 0\nAs nan 1 1\n",
        encoding="utf-8",
    )
    adapter = create_adapter(path)
    with pytest.raises(AppError) as exc:
        adapter.get_frame(0)
    assert exc.value.code == INVALID_DATASET
    assert "row 1: non-finite position" in str(exc.value)


def test_extxyz_resolves_species_tokens_and_refuses_to_guess(tmp_path: Path) -> None:
    """An unresolvable token used to land on Z=0, which the radii table then
    promoted to hydrogen — a misread column silently relabelled the whole
    structure. Numeric charges and case variants are still accepted."""
    def read(body: str):
        path = tmp_path / f"{abs(hash(body))}.xyz"
        path.write_text(
            '1\nLattice="10 0 0 0 10 0 0 0 10" Properties=species:S:1:pos:R:3\n' + body,
            encoding="utf-8",
        )
        return create_adapter(path).get_frame(0)

    assert read("Si 0 0 0\n").numbers.tolist() == [14]
    assert read("si 0 0 0\n").numbers.tolist() == [14]
    assert read("14 0 0 0\n").numbers.tolist() == [14]
    with pytest.raises(AppError) as exc:
        read("Xx 0 0 0\n")
    assert exc.value.code == INVALID_DATASET
    assert "unknown species" in str(exc.value)


def test_extxyz_pbc_accepts_the_spellings_writers_emit_and_refuses_the_rest(tmp_path: Path) -> None:
    """`pbc = tuple(v.upper() == "T")` read anything but a bare T as False - and a
    frame with no periodic axis has its cell zeroed, so a hand-edited or non-ASE
    `pbc="True True True"` crystal was silently analysed as an isolated cluster:
    vacuum between every pair, no volume in the statistics, ghosts bonded through
    the empty direction. Omitting the key was strictly better, because that
    infers periodicity from the lattice."""
    def read(flag: str):
        path = tmp_path / f"pbc_{abs(hash(flag))}.xyz"
        path.write_text(
            '2\nLattice="5 0 0 0 5 0 0 0 5" pbc="' + flag + '" '
            "Properties=species:S:1:pos:R:3\nSi 0 0 0\nSi 2.5 0 0\n",
            encoding="utf-8",
        )
        return create_adapter(path).get_frame(0)

    for spelling in ("T T T", "True True True", "true true true", "1 1 1", "yes yes yes"):
        frame = read(spelling)
        assert bool(frame.pbc.all()), spelling
        assert abs(float(np.linalg.det(frame.cell))) == pytest.approx(125.0), spelling
    for spelling in ("F F F", "False False False", "0 0 0", "n n n"):
        frame = read(spelling)
        assert not bool(frame.pbc.any()), spelling
        assert not frame.cell.any(), "an isolated frame keeps no cell"
    assert read("T F T").cell is not None  # mixed still flattens to periodic: ADR-28
    for garbage in ("X X X", "maybe yes no", "T T"):
        with pytest.raises(AppError) as exc:
            read(garbage)
        assert exc.value.code == INVALID_DATASET, garbage


def test_extxyz_refuses_a_number_spelling_xyz_cannot_mean(tmp_path: Path) -> None:
    # float() and int() accept PEP 515 underscores and any Unicode decimal digit,
    # so "1_0.0" parsed as 10.0 and a fullwidth １０.０ likewise: the file text said
    # one thing and the structure got another, with no error to trace.
    def read(token: str):
        path = tmp_path / f"num_{abs(hash(token))}.xyz"
        path.write_text(
            '1\nLattice="10 0 0 0 10 0 0 0 10" pbc="F F F" Properties=species:S:1:pos:R:3\n'
            + f"Si {token} 0 0\n",
            encoding="utf-8",
        )
        return create_adapter(path).get_frame(0)

    assert read("2.5").positions[0][0] == 2.5
    for token in ("1_0.0", "１０.0", "1_0"):
        with pytest.raises(AppError) as exc:
            read(token)
        assert exc.value.code == INVALID_DATASET, token
    # An underscore is only forbidden inside a number; a species token is still
    # whatever the symbol table knows, and a comment may hold any text at all.
    assert read("2.5").numbers.tolist() == [14]


def test_extxyz_opens_a_file_saved_with_a_byte_order_mark(tmp_path: Path) -> None:
    """Notepad's default save prepends a BOM. Read as plain utf-8 it stays in the
    first line's text, int() fails, and the parser concluded the frame boundaries
    had desynchronised - a wrong diagnosis for a structurally perfect file, and a
    dataset that could never be opened."""
    body = (
        '1\nLattice="10 0 0 0 10 0 0 0 10" pbc="T T T" Properties=species:S:1:pos:R:3\n'
        "Si 0 0 0\n"
    )
    plain = tmp_path / "plain.xyz"
    plain.write_text(body, encoding="utf-8")
    bom = tmp_path / "bom.xyz"
    bom.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))

    assert len(create_adapter(bom)) == len(create_adapter(plain)) == 1
    assert create_adapter(bom).get_frame(0).numbers.tolist() == [14]


def test_deepmd_size_gate_counts_every_file_exactly_once(tmp_path: Path, monkeypatch) -> None:
    """The pre-flight guard added coord.npy twice (once by name, once again in the
    walk over set.000) while never counting root files other than type.raw, so it
    enforced a larger quantity than the advertised cap and refused datasets that
    fit under it - and disagreed with scan(), which reports the true total."""
    from make_fixtures import write_deepmd

    import mdescriptor_studio_backend.datasets.deepmd as deepmd_module

    source = tmp_path / "deepmd"
    write_deepmd(source, 4, 8, seed=5)
    true_total = sum(item.stat().st_size for item in source.rglob("*") if item.is_file())
    coord = (source / "set.000" / "coord.npy").stat().st_size
    assert coord > (source / "type_map.raw").stat().st_size, "the test needs the double count to dominate"

    monkeypatch.setattr(deepmd_module, "MAX_DEEPMD_BYTES", true_total)
    assert len(create_adapter(source)) == 4, "a dataset exactly at the cap must open"

    monkeypatch.setattr(deepmd_module, "MAX_DEEPMD_BYTES", true_total - 1)
    with pytest.raises(AppError) as exc:
        create_adapter(source)
    assert exc.value.code == INVALID_DATASET, "the gate is still a gate"


def test_builtin_formats_are_registered() -> None:
    assert {"deepmd", "extxyz"}.issubset(set(reader_formats()))


def test_reader_interface_exposes_scan_and_frames() -> None:
    # One spelling for the reader contract: scan() counts, iter_frames() walks,
    # and the two agree on the same frames. The adapter also used to carry
    # metadata()/read()/iterate_frames() aliases that nothing in services ever
    # called, so a new format could satisfy one spelling and break the other -
    # and read() materialised every frame of a 250 000-frame source at once.
    reader = create_reader(DATA)
    metadata = reader.scan()
    frames = list(reader.iter_frames())
    assert metadata.number_of_frames == len(reader) == len(frames)
    assert [frame.index for frame in frames] == list(range(metadata.number_of_frames))


def test_detection_path_uses_registry() -> None:
    adapter = create_adapter(DATA)
    assert adapter.format_name == "extxyz"


def test_new_reader_registration_needs_no_factory_branch(monkeypatch) -> None:
    sentinel = object()

    def factory(path: Path):
        assert path == Path("anything.xyz")
        return sentinel

    monkeypatch.setitem(reader_registry._READERS, "pytest-dummy", factory)
    assert create_reader(Path("anything.xyz"), "pytest-dummy") is sentinel
    assert "pytest-dummy" in reader_formats()
