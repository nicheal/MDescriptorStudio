"""Analysis algorithm registry.

The registry is the only place that knows the public algorithm vocabulary and
the numerical call shape. Services can dispatch by metadata without growing a
second central catalogue or a chain of ``if analysis_type`` branches.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, Sequence


Handler = Callable[..., dict]


class AnalysisAlgorithm(Protocol):
    name: str
    category: str

    def validate(self, params: dict) -> None: ...

    def run(self, data, params: dict) -> dict: ...


@dataclass(frozen=True)
class AlgorithmSpec:
    name: str
    category: str
    handler: Handler | None = None
    schema: dict[str, Any] = field(default_factory=dict)
    input_count: int = 1
    variant: str | None = None
    aliases: tuple[str, ...] = ()


class AnalysisRegistry:
    def __init__(self, specs: Sequence[AlgorithmSpec] = ()) -> None:
        self._specs: dict[str, AlgorithmSpec] = {}
        self._aliases: dict[str, str] = {}
        for spec in specs:
            self.register(spec)

    def register(self, algorithm: AlgorithmSpec | AnalysisAlgorithm, **metadata: Any) -> AlgorithmSpec:
        """Register a spec or a small plugin object with ``run``.

        ``metadata`` is intentionally optional so a future descriptor/plugin
        can register itself with only ``name``, ``category`` and ``run``.
        """
        if isinstance(algorithm, AlgorithmSpec):
            spec = algorithm
        else:
            name = str(getattr(algorithm, "name"))
            spec = AlgorithmSpec(
                name=name,
                category=str(metadata.get("category", getattr(algorithm, "category", "engine"))),
                handler=getattr(algorithm, "run"),
                schema=dict(metadata.get("schema", getattr(algorithm, "schema", {}) or {})),
                input_count=int(metadata.get("input_count", getattr(algorithm, "input_count", 1))),
                variant=metadata.get("variant"),
                aliases=tuple(metadata.get("aliases", getattr(algorithm, "aliases", ()) or ())),
            )
        name = spec.name.strip().lower()
        if not name or name in self._specs:
            raise ValueError(f"analysis algorithm {spec.name!r} is already registered")
        normalized = AlgorithmSpec(
            name=name,
            category=spec.category,
            handler=spec.handler,
            schema=dict(spec.schema),
            input_count=spec.input_count,
            variant=spec.variant,
            aliases=tuple(alias.strip().lower() for alias in spec.aliases),
        )
        self._specs[name] = normalized
        for alias in normalized.aliases:
            if alias and alias not in self._specs and alias not in self._aliases:
                self._aliases[alias] = name
        return normalized

    def get(self, name: str) -> AlgorithmSpec:
        key = str(name).strip().lower()
        key = self._aliases.get(key, key)
        try:
            return self._specs[key]
        except KeyError as exc:
            raise KeyError(f"unknown analysis algorithm: {name}") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(self._specs)

    def specs(self) -> tuple[AlgorithmSpec, ...]:
        return tuple(self._specs.values())

    def catalog(self) -> dict[str, str]:
        return {spec.name: spec.category for spec in self._specs.values()}

    def names_by_category(self, *categories: str) -> frozenset[str]:
        wanted = set(categories)
        return frozenset(spec.name for spec in self._specs.values() if spec.category in wanted)

    def run(self, name: str, samples: Sequence[Any], params: dict, progress=None, **context: Any) -> dict:
        """Invoke a registered algorithm using its declared input shape."""
        spec = self.get(name)
        if spec.handler is None:
            raise KeyError(f"analysis algorithm {name!r} is an RPC wrapper")
        if len(samples) < spec.input_count:
            raise ValueError(f"{name} requires {spec.input_count} input sample matrix(es)")
        handler = spec.handler
        if spec.category == "engine":
            return handler(samples[0], params, progress)
        if spec.category == "pair":
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
            return handler(context.get("runs", list(zip([], samples))), params, progress)
        if spec.category == "perturbation":
            runner = context.get("runner")
            if runner is None:
                raise ValueError("perturbation algorithm requires a runner context")
            return runner()
        raise ValueError(f"unsupported analysis algorithm category: {spec.category}")


def build_default_registry(engine=None) -> AnalysisRegistry:
    """Build the built-in registry.

    ``engine`` is accepted for backwards compatibility with older call sites
    (the stable ``AnalysisEngine`` facade). Plugin implementations are the
    source of truth now, so it is deliberately ignored.
    """
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
