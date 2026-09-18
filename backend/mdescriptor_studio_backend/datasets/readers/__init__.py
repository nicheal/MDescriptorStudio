"""Reader registrations for the built-in dataset formats.

A reader is any :class:`~datasets.base.DatasetAdapter`: ``metadata()``,
``read()`` and ``iterate_frames()`` are the stable names the service layer
uses. Supporting LAMMPS/VASP/ASE therefore means adding one
:func:`..registry.register_reader` call here, not another ``if format == ...``
branch in the callers.
"""

from __future__ import annotations

from ..deepmd import DeepMDAdapter
from ..extxyz import ExtXYZAdapter
from .registry import create_reader, reader_formats, register_reader

register_reader("deepmd", DeepMDAdapter)
register_reader("extxyz", ExtXYZAdapter)

__all__ = ["create_reader", "reader_formats", "register_reader"]
