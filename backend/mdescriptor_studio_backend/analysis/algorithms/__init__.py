"""Built-in analysis algorithm implementations.

Each module owns one numerical method and is registered through
:mod:`..registry`; the warmup gate lives in :mod:`._common`.
"""

from ._common import arm_analysis_warmup_gate, warmup

__all__ = ["arm_analysis_warmup_gate", "warmup"]
