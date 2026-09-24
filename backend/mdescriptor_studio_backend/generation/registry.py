"""Generation vocabulary registry.

The single place that knows the public names for operators, objectives and
optimizers. The service validates requests through it; ``catalog()`` feeds
the generation.catalog RPC that drives the UI schema — so the frontend can
never offer a combination the backend cannot run.
"""

from __future__ import annotations

from .constraints.geometry import GeometryConstraints
from .models import OperatorSpec
from .objectives import (
    CompositeObjective,
    CoverageGainObjective,
    LocalEnvironmentNoveltyObjective,
    NoveltyObjective,
)
from .operators import AnisotropicStrain, AtomicDisplacement, CellShear, IsotropicStrain
from .optimizers import RandomSearchOptimizer


class GenerationRegistry:
    def __init__(self) -> None:
        self._operators = {
            cls.name: cls
            for cls in (
                AtomicDisplacement,
                IsotropicStrain,
                AnisotropicStrain,
                CellShear,
            )
        }
        self._objectives = {
            cls.name: cls
            for cls in (
                NoveltyObjective,
                LocalEnvironmentNoveltyObjective,
                CompositeObjective,
                CoverageGainObjective,
            )
        }
        self._optimizers = {RandomSearchOptimizer.name: RandomSearchOptimizer}

    # -- lookup ------------------------------------------------------------
    def operator(self, name: str):
        try:
            return self._operators[str(name)]
        except KeyError as exc:
            raise KeyError(f"unknown generation operator: {name}") from exc

    def objective(self, name: str):
        try:
            return self._objectives[str(name)]
        except KeyError as exc:
            raise KeyError(f"unknown generation objective: {name}") from exc

    def optimizer(self, name: str):
        try:
            return self._optimizers[str(name)]
        except KeyError as exc:
            raise KeyError(f"unknown generation optimizer: {name}") from exc

    # -- construction --------------------------------------------------------
    def build_operator(self, spec: OperatorSpec):
        instance = self.operator(spec.name)()
        instance.operator_params = dict(spec.params)
        return instance

    def build_objective(self, objective: dict):
        # Scaling is handled by the descriptor archives, not objective constructors.
        params = {k: v for k, v in dict(objective).items() if k not in ("type", "scaling")}
        return self.objective(objective["type"])(**params)

    def build_optimizer(self, name: str, operators: list, params: dict):
        params = dict(params or {})
        params.pop("n_seeds", None)  # engine-level knob, not an optimizer ctor arg
        return self.optimizer(name)(operators, **params)

    # -- catalog -------------------------------------------------------------
    def catalog(self) -> dict:
        """UI schema: the full generation vocabulary with parameter defaults."""
        return {
            "optimizers": [
                {
                    "name": RandomSearchOptimizer.name,
                    "params": {
                        "children_per_seed": 8,
                        "batch_accept": 8,
                    },
                }
            ],
            "objectives": [
                {"name": NoveltyObjective.name, "params": {}},
                {
                    "name": LocalEnvironmentNoveltyObjective.name,
                    "params": {
                        "aggregation": ["mean", "top_fraction_mean", "quantile", "max"],
                        "top_fraction": 0.2,
                        "quantile": 0.5,
                        "novelty_threshold": 0.25,
                    },
                },
                {
                    "name": CompositeObjective.name,
                    "params": {
                        "structure_weight": 0.3,
                        "local_weight": 0.7,
                        "aggregation": "top_fraction_mean",
                        "top_fraction": 0.2,
                        "novelty_threshold": 0.25,
                    },
                },
                {
                    "name": CoverageGainObjective.name,
                    "params": {},
                },
            ],
            "operators": [
                {"name": AtomicDisplacement.name, "params": {"max_sigma": 0.15}},
                {"name": IsotropicStrain.name, "params": {"max_strain": 0.05}},
                {"name": AnisotropicStrain.name, "params": {"max_strain": 0.05}},
                {"name": CellShear.name, "params": {"max_shear": 0.05}},
            ],
            "constraints": {
                "min_distance_mode": ["none", "absolute", "covalent"],
                "min_distance_factor": 0.7,
                "max_volume_change": 0.2,
                "composition_locked": True,
                "atom_count_locked": True,
            },
            "budget": {
                "max_evaluations": 10_000,
                "max_accepted": 500,
                "max_generations": 200,
                "target_novelty": None,
                "no_improvement_rounds": 10,
            },
        }


GENERATION_REGISTRY = GenerationRegistry()
GENERATION_ALGORITHM_VERSION = "gen-3"

__all__ = ["GENERATION_REGISTRY", "GENERATION_ALGORITHM_VERSION", "GenerationRegistry"]
