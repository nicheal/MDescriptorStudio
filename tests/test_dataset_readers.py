"""Dataset reader interface and registry tests."""

from __future__ import annotations

from pathlib import Path

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


def test_builtin_formats_are_registered() -> None:
    assert {"deepmd", "extxyz"}.issubset(set(reader_formats()))


def test_reader_interface_exposes_metadata_and_frames() -> None:
    reader = create_reader(DATA)
    metadata = reader.metadata()
    assert metadata.number_of_frames == len(reader)
    frames = reader.read()
    assert len(frames) == metadata.number_of_frames
    assert [frame.index for frame in reader.iterate_frames()] == list(range(metadata.number_of_frames))


def test_detection_path_uses_registry() -> None:
    adapter = create_adapter(DATA)
    assert adapter.format_name == "extxyz"
    assert adapter.metadata().number_of_frames == adapter.scan().number_of_frames


def test_new_reader_registration_needs_no_factory_branch(monkeypatch) -> None:
    sentinel = object()

    def factory(path: Path):
        assert path == Path("anything.xyz")
        return sentinel

    monkeypatch.setitem(reader_registry._READERS, "pytest-dummy", factory)
    assert create_reader(Path("anything.xyz"), "pytest-dummy") is sentinel
    assert "pytest-dummy" in reader_formats()
