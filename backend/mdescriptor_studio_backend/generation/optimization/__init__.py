"""Optimizer-facing data contracts (G3.5 optimizer lifecycle refactor).

    optimizer.initialize(context)
    while budget:
        ProposalBatch → engine pipeline (constraint → descriptor → objective
                        → selection → archive) → ObservationBatch
        optimizer.observe(observations)

Random, GA, PSO and External optimizers share this loop; the engine keeps
ownership of constraints, descriptor evaluation, objectives, the shared
archives and final acceptance.
"""

from .context import OptimizationContext
from .observation import CandidateObservation, ObservationBatch
from .proposal import ProposalBatch

__all__ = ["OptimizationContext", "CandidateObservation", "ObservationBatch", "ProposalBatch"]
