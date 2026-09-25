"""What an optimizer hands back to the engine each round."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProposalBatch:
    """One round of proposed candidates, in deterministic proposal order.

    The engine evaluates every candidate through geometry constraints →
    descriptor → objective → selection and reports each outcome back through
    :meth:`Optimizer.observe`. ``notes`` carries optional optimizer-private
    metadata (e.g. a GA genome id) that the engine must not interpret.
    """

    candidates: list = field(default_factory=list)
    notes: dict = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.candidates)
