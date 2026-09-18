"""Analysis algorithm registry.

The registry is the only place that knows the public algorithm vocabulary and
the numerical call shape: services dispatch through it instead of keeping a
second central catalogue of algorithm names.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol, Sequence


Handler = Callable[..., dict]


class AnalysisAlgorithm(Protocol):
    name: str
    category: str

    def run(self, data, params: dict) -> dict: ...


@dataclass(frozen=True)
class AlgorithmSpec:
    name: str
    category: str
    handler: Handler | None = None
    variant: str | None = None


class AnalysisRegistry:
    def __init__(self, specs: Sequence[AlgorithmSpec] = ()) -> None:
        self._specs: dict[str, AlgorithmSpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, algorithm: AlgorithmSpec | AnalysisAlgorithm) -> AlgorithmSpec:
        """Register a spec, or a plugin object exposing name/category/run."""
        if isinstance(algorithm, AlgorithmSpec):
            spec = algorithm
        else:
            spec = AlgorithmSpec(
                name=str(algorithm.name),
                category=str(algorithm.category),
                handler=algorithm.run,
            )
        name = spec.name.strip().lower()
        if not name or name in self._specs:
            raise ValueError(f"analysis algorithm {spec.name!r} is already registered")
        normalized = AlgorithmSpec(name=name, category=spec.category, handler=spec.handler, variant=spec.variant)
        self._specs[name] = normalized
        return normalized

    def get(self, name: str) -> AlgorithmSpec:
        key = str(name).strip().lower()
        try:
            return self._specs[key]
        except KeyError as exc:
            raise KeyError(f"unknown analysis algorithm: {name}") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(self._specs)

    def catalog(self) -> dict[str, str]:
        return {spec.name: spec.category for spec in self._specs.values()}

    def run(self, name: str, samples: Sequence[Any], params: dict, progress=None, **context: Any) -> dict:
        """Invoke a registered algorithm using its declared input shape."""
        spec = self.get(name)
        if spec.handler is None:
            raise KeyError(f"analysis algorithm {name!r} is an RPC wrapper")
        if not samples:
            raise ValueError(f"{name} requires an input sample matrix")
        handler = spec.handler
        if spec.category == "engine":
            return handler(samples[0], params, progress)
        if spec.category == "pair":
            if len(samples) < 2:
                raise ValueError(f"{name} requires two input sample matrices")
            return handler(samples[0], samples[1], params, progress)
        if spec.category in ("cluster", "outlier"):
            return handler(samples[0], params, spec.variant or spec.name, progress)
        if spec.category == "sampling":
            return handler(
                samples[0],
                params,
                spec.variant or spec.name,
                progress,
                existing=context.get("existing"),
                group_labels=context.get("group_labels"),
                blocks=context.get("blocks"),
                existing_blocks=context.get("existing_blocks"),
            )
        if spec.category == "sensitivity":
            runs = context.get("runs")
            if not runs:
                raise ValueError(f"{name} algorithm requires a runs context")
            return handler(runs, params, progress)
        if spec.category == "perturbation":
            runner = context.get("runner")
            if runner is None:
                raise ValueError("perturbation algorithm requires a runner context")
            return runner()
        raise ValueError(f"unsupported analysis algorithm category: {spec.category}")


def build_default_registry() -> AnalysisRegistry:
    """Build the built-in registry from the plugin implementations."""
    from .algorithms.correlation import FeatureCorrelation, PropertyCorrelation
    from .algorithms.kernel import Kernel
    from .algorithms.pairs import Acquisition, Compare, Coverage, Drift, Mantel, Overlap
    from .algorithms.pca import PCA
    from .algorithms.sensitivity import PerturbationSensitivity, Sensitivity
    from .algorithms.tsne import TSNE
    from .algorithms.umap import UMAP
    from .clustering import Cluster, Outlier
    from .metrics import (
        EffectiveDimension,
        FeatureVariance,
        LocalDiversity,
        Neighbors,
        Pairwise,
        Similarity,
        Trajectory,
    )
    from .sampling.engine import Sampling

    registry = AnalysisRegistry()
    plugins = (
        PCA(),
        UMAP(),
        TSNE(),
        Kernel(),
        FeatureCorrelation(),
        PropertyCorrelation(),
        Neighbors(),
        Similarity(),
        Pairwise(),
        FeatureVariance(),
        LocalDiversity(),
        EffectiveDimension(),
        Trajectory(),
        Coverage(),
        Overlap(),
        Acquisition(),
        Mantel(),
        Compare(),
        Drift(),
        Sensitivity(),
        PerturbationSensitivity(),
    )
    for plugin in plugins:
        registry.register(plugin)

    cluster = Cluster()
    for name in ("kmeans", "dbscan", "hdbscan", "agglomerative", "hierarchical"):
        registry.register(AlgorithmSpec(name, "cluster", cluster.run, variant=name))

    outlier = Outlier()
    for name in (
        "knn",
        "lof",
        "isolation_forest",
        "isolation-forest",
        "iforest",
        "mahalanobis",
        "mahalanobis_distance",
    ):
        registry.register(AlgorithmSpec(name, "outlier", outlier.run, variant=name))

    sampling = Sampling()
    for name in ("fps", "random", "stratified", "cluster_representative", "per_element", "element"):
        registry.register(AlgorithmSpec(name, "sampling", sampling.run, variant=name))

    # These names are wrapper RPC entry points, not numerical algorithms. They
    # remain discoverable so the protocol surface stays explicit.
    for name in ("cluster", "outlier", "sampling"):
        registry.register(AlgorithmSpec(name, "wrapper"))
    return registry


__all__ = ["build_default_registry"]
