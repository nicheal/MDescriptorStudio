"""Dataset reader interface and registry tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from mdescriptor_studio_backend.datasets import create_adapter
from mdescriptor_studio_backend.datasets.readers import create_reader, reader_formats
from mdescriptor_studio_backend.datasets.readers import registry as reader_registry


DATA = Path(__file__).resolve().parent / "data" / "extxyz_small.xyz"


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
