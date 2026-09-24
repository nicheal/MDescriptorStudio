"""Random point-defect mutations for generated structures."""

from __future__ import annotations

import numpy as np

from ...datasets.deepmd_symbols import _SYMBOL_TO_Z, _Z_TO_SYMBOL
from ..models import StructureCandidate


def _configured_element(params: dict) -> int | None:
    symbol = params.get("element")
    if symbol is None:
        return None
    symbol = str(symbol).strip()
    symbol = symbol[:1].upper() + symbol[1:].lower()
    if symbol not in _SYMBOL_TO_Z:
        raise ValueError("element must be a valid chemical symbol")
    return _SYMBOL_TO_Z[symbol]


def _random_interstitial_position(parent: StructureCandidate, rng: np.random.Generator) -> np.ndarray:
    cell = np.asarray(parent.cell, dtype=np.float64)
    if bool(np.asarray(parent.pbc, dtype=bool).all()) and abs(float(np.linalg.det(cell))) > 1e-10:
        return rng.random(3) @ cell
    positions = np.asarray(parent.positions, dtype=np.float64)
    if positions.size == 0:
        return rng.uniform(-2.0, 2.0, size=3)
    return rng.uniform(positions.min(axis=0) - 2.0, positions.max(axis=0) + 2.0)


def _candidate_id(parent: StructureCandidate, tag: str, rng: np.random.Generator) -> str:
    return f"{parent.candidate_id}_{tag}{int(rng.integers(0, 2**31))}"


class Vacancy:
    """Remove one randomly selected atom while leaving at least one atom."""

    name = "vacancy"

    def can_apply(self, parent: StructureCandidate, params: dict) -> bool:
        return np.asarray(parent.atomic_numbers).size > 1

    def apply(self, parent: StructureCandidate, rng: np.random.Generator, params: dict) -> StructureCandidate:
        numbers = np.asarray(parent.atomic_numbers, dtype=np.int64)
        if numbers.size <= 1:
            raise ValueError("a vacancy requires a parent with at least two atoms")
        index = int(rng.integers(numbers.size))
        keep = np.arange(numbers.size) != index
        return parent.child(
            candidate_id=_candidate_id(parent, "v", rng),
            positions=np.asarray(parent.positions, dtype=np.float64)[keep],
            atomic_numbers=numbers[keep],
            operator=self.name,
            operator_params={"removed_index": index, "removed_element": _Z_TO_SYMBOL[int(numbers[index])]},
            metadata={"defect": "vacancy", "removed_index": index},
        )


class InterstitialAtom:
    """Add a randomly selected atom inside the periodic cell or near the cluster."""

    name = "interstitial_atom"

    def can_apply(self, parent: StructureCandidate, params: dict) -> bool:
        return bool(np.asarray(parent.atomic_numbers).size) or _configured_element(params) is not None

    def apply(self, parent: StructureCandidate, rng: np.random.Generator, params: dict) -> StructureCandidate:
        numbers = np.asarray(parent.atomic_numbers, dtype=np.int64)
        element_number = _configured_element(params)
        if element_number is None:
            if numbers.size == 0:
                raise ValueError("an interstitial requires a parent element or an explicit element")
            elements = np.unique(numbers)
            element_number = int(elements[int(rng.integers(elements.size))])
        symbol = _Z_TO_SYMBOL[element_number]
        position = _random_interstitial_position(parent, rng)
        return parent.child(
            candidate_id=_candidate_id(parent, "i", rng),
            positions=np.vstack((np.asarray(parent.positions, dtype=np.float64), position)),
            atomic_numbers=np.append(numbers, element_number),
            operator=self.name,
            operator_params={"element": symbol, "position": position.tolist()},
            metadata={"defect": "interstitial", "added_element": symbol},
        )


class Substitution:
    """Replace one atom with a configured element or another existing species."""

    name = "substitution"

    def can_apply(self, parent: StructureCandidate, params: dict) -> bool:
        numbers = np.asarray(parent.atomic_numbers, dtype=np.int64)
        target = _configured_element(params)
        if target is not None:
            return bool(numbers.size and np.any(numbers != target))
        return np.unique(numbers).size > 1

    def apply(self, parent: StructureCandidate, rng: np.random.Generator, params: dict) -> StructureCandidate:
        numbers = np.asarray(parent.atomic_numbers, dtype=np.int64)
        target = _configured_element(params)
        if target is None:
            if np.unique(numbers).size <= 1:
                raise ValueError("automatic substitution requires a multicomponent parent")
            index = int(rng.integers(numbers.size))
            alternatives = np.unique(numbers[numbers != numbers[index]])
            target = int(alternatives[int(rng.integers(alternatives.size))])
        else:
            sites = np.flatnonzero(numbers != target)
            if sites.size == 0:
                raise ValueError("substitution target must differ from at least one parent atom")
            index = int(sites[int(rng.integers(sites.size))])
        new_numbers = numbers.copy()
        new_numbers[index] = target
        symbol = _Z_TO_SYMBOL[target]
        return parent.child(
            candidate_id=_candidate_id(parent, "sub", rng),
            positions=np.asarray(parent.positions, dtype=np.float64),
            atomic_numbers=new_numbers,
            operator=self.name,
            operator_params={"index": index, "element": symbol},
            metadata={"defect": "substitution", "index": index, "element": symbol},
        )


class AntisiteSwap:
    """Swap two unlike species to create a composition-preserving antisite pair."""

    name = "antisite_swap"

    def can_apply(self, parent: StructureCandidate, params: dict) -> bool:
        return np.unique(np.asarray(parent.atomic_numbers, dtype=np.int64)).size > 1

    def apply(self, parent: StructureCandidate, rng: np.random.Generator, params: dict) -> StructureCandidate:
        numbers = np.asarray(parent.atomic_numbers, dtype=np.int64)
        if np.unique(numbers).size <= 1:
            raise ValueError("an antisite swap requires a multicomponent parent")
        first = int(rng.integers(numbers.size))
        unlike = np.flatnonzero(numbers != numbers[first])
        second = int(unlike[int(rng.integers(unlike.size))])
        new_numbers = numbers.copy()
        new_numbers[first], new_numbers[second] = new_numbers[second], new_numbers[first]
        return parent.child(
            candidate_id=_candidate_id(parent, "a", rng),
            positions=np.asarray(parent.positions, dtype=np.float64),
            atomic_numbers=new_numbers,
            operator=self.name,
            operator_params={"indices": [first, second]},
            metadata={"defect": "antisite_pair", "indices": [first, second]},
        )
