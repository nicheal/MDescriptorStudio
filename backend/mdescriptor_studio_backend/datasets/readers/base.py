"""Dataset reader interface.

The reader registry keeps format-specific construction out of the service and
detection layers. Adding LAMMPS/VASP/ASE support means registering one factory
in :mod:`datasets.readers.registry`, not growing another ``if format == ...``
branch in the callers.

The concrete readers still live in their original format modules to avoid an
unnecessary wrapper layer; :class:`DatasetReader` is the structural contract
they already satisfy.
"""

from __future__ import annotations

from ..base import DatasetAdapter, DatasetFrame, ScanMeta

# A reader is any DatasetAdapter: metadata()/read()/iterate_frames() are the
# stable names, while scan()/get_frame()/iter_frames() stay for compatibility.
DatasetReader = DatasetAdapter

__all__ = ["DatasetReader", "DatasetAdapter", "DatasetFrame", "ScanMeta"]
