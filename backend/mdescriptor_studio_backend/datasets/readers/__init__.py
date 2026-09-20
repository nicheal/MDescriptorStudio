"""Reader registrations for the built-in dataset formats.

A reader is any :class:`~datasets.base.DatasetAdapter`; the service layer drives
them through ``scan()``, ``get_frame()`` and ``iter_frames()`` - the one
spelling, so a new format cannot grow a second way to be read. Supporting
LAMMPS/VASP/ASE therefore means adding one :func:`..registry.register_reader`
call here, not another ``if format == ...`` branch in the callers.
"""

from __future__ import annotations

from ..deepmd import DeepMDAdapter
from ..extxyz import ExtXYZAdapter
from .registry import create_reader, reader_formats, register_reader

register_reader("deepmd", DeepMDAdapter)
register_reader("extxyz", ExtXYZAdapter)

__all__ = ["create_reader", "reader_formats", "register_reader"]
