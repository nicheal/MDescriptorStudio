"""Reader registrations for the built-in dataset formats.

LAMMPS/ASE/VASP registrations belong in their own modules and only need to
call :func:`..registry.register_reader`.
"""

from __future__ import annotations

from .base import DatasetReader
from .deepmd import DeepMDAdapter, create as create_deepmd
from .extxyz import ExtXYZAdapter, create as create_extxyz
from .registry import create_reader, reader_formats, register_reader

register_reader("deepmd", create_deepmd)
register_reader("extxyz", create_extxyz)

__all__ = [
    "DatasetReader",
    "DeepMDAdapter",
    "ExtXYZAdapter",
    "create_reader",
    "reader_formats",
    "register_reader",
]
