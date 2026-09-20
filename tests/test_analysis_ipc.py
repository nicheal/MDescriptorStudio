"""IPC contract coverage for the Analysis method catalogue."""

from __future__ import annotations

import re
from pathlib import Path

from make_fixtures import write_deepmd

from conftest import BackendProcess, wait_job

REGISTRY = Path(__file__).resolve().parents[1] / "frontend" / "src" / "features" / "analysis" / "registry.ts"


def artifact_arrays_by_kind() -> dict[str, list[str]]:
    """The frontend's fetch table, parsed: {preview kind: [array names]}.

    Read from the TypeScript rather than restated here, because the point of the
    assertion is that the two lists agree - a copy would drift the same way.
    """
    block = re.search(r"export const ARTIFACT_ARRAYS[^=]*=\s*\{(.*?)\n\};", REGISTRY.read_text(encoding="utf-8"), re.S)
    assert block, "registry.ts no longer declares ARTIFACT_ARRAYS in one table"
    entries = re.findall(r"(\w+):\s*\[(.*?)\]", block.group(1), re.S)
    arrays = {kind: re.findall(r'"([^"]+)"', names) for kind, names in entries}
    assert len(arrays) >= 10, arrays
    return {kind: names for kind, names in arrays.items() if names}


def _view_bodies() -> dict[str, str]:
    """The source of every component in the result-view files, by name."""
    bodies: dict[str, str] = {}
    pages = REGISTRY.parents[2] / "pages"
    for path in sorted(pages.glob("*.tsx")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"^(?:export\s+)?(?:default\s+)?function (\w+)\(", text, re.M):
            depth, index = 0, text.index("(", match.start())
            while True:
                if text[index] == "(":
                    depth += 1
                elif text[index] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                index += 1
            follow = re.search(r"^(?:export\s+)?(?:default\s+)?function ", text[index:], re.M)
            bodies[match.group(1)] = text[index: index + (follow.start() if follow else len(text))]
    return bodies


def _dispatcher() -> tuple[dict[str, tuple[str, str]], list[str]]:
    """The view dispatcher, as {preview kind: (component, its dispatch line)}.

    The line is kept because MatrixView is handed matrix(arrays.similarity_matrix)
    there rather than reading `arrays` inside its own body.
    """
    lines = (REGISTRY.parents[2] / "pages" / "analysisVisualizations.tsx").read_text(encoding="utf-8").splitlines()
    mapping: dict[str, tuple[str, str]] = {}
    for index, line in enumerate(lines):
        match = re.search(r'if \((kind === "[a-z_"]+(?: \|\| kind === "[a-z_"]+")*)\) return <(\w+)', line)
        if match:
            for kind in re.findall(r'"([a-z_]+)"', match.group(1)):
                mapping[kind] = (match.group(2), line)
    return mapping, lines


def test_every_fetched_array_is_read_by_the_view_that_fetches_it() -> None:
    """registry.ts's own comment says a name on this table is a full chunk
    round-trip, and AnalysisResultVisualization gates the whole panel on the
    fetch finishing - so an array no view reads is blank time charged to a
    result the user just asked for, and the local-environment panel carried
    three of them (coords, sample_indices and labels, for a 20 000-row
    preview). The forward direction is checked against real artifacts elsewhere;
    this is the other half, and it reads the TypeScript rather than restating it.

    Two tiers, because only part of the table is reachable from the dispatcher:
    a kind `Visualization` routes is checked against *that component*, while a
    kind rendered elsewhere (feature_variance, effective_dimension) is checked
    against the whole pages tree. The precise tier catches "declared for X, read
    only by Y"; the fallback tier only catches "read by nobody" - and says so
    rather than pretending to more than it can see."""
    bodies, per_kind = _view_bodies(), _dispatcher()[0]
    pages = REGISTRY.parents[2] / "pages"
    everywhere = "".join(path.read_text(encoding="utf-8") for path in sorted(pages.glob("*.ts*")))
    strict, fallback = [], []
    for kind, names in sorted(artifact_arrays_by_kind().items()):
        if kind in per_kind:
            component, dispatch_line = per_kind[kind]
            assert component in bodies, f"{component} was not found in frontend/src/pages"
            read = set(re.findall(r"arrays\.([a-z_][a-z0-9_]*)", bodies[component] + dispatch_line))
            strict += [f"{kind}.{name}" for name in names if name not in read]
        else:
            read = set(re.findall(r"arrays\.([a-z_][a-z0-9_]*)", everywhere))
            fallback += [f"{kind}.{name}" for name in names if name not in read]
    assert strict == [], f"fetched but never read by the view that fetches them: {strict}"
    assert fallback == [], f"fetched but read by nothing under frontend/src/pages: {fallback}"


def test_analysis_method_catalog_over_ipc(tmp_path: Path) -> None:
    ds_dir = tmp_path / "gaas"
    write_deepmd(ds_dir, 8, 16, seed=51)
    bp = BackendProcess(tmp_path)
    try:
        assert bp.read_line()["event"] == "backend.ready"
        registered = bp.request(1, "dataset.register", {"path": str(ds_dir), "name": "analysis-ipc"})
        ds_done = wait_job(bp, registered["result"]["job_id"])
        dataset_id = ds_done["result"]["dataset_id"]
        submitted = bp.request(
            2,
            "descriptor.submit",
            {
                "dataset_id": dataset_id,
                "descriptor_name": "ACE",
                "parameters": {"species": [31, 33], "N": 1},
                "scope": "dataset",
            },
        )
        descriptor_done = wait_job(bp, submitted["result"]["job_id"], timeout=300)
        assert descriptor_done["status"] == "COMPLETED", descriptor_done
        run_id = descriptor_done["result"]["run_id"]

        sequence = 10

        def run(method: str, params: dict) -> str:
            nonlocal sequence
            response = bp.request(sequence, method, params)
            sequence += 1
            assert "result" in response, response
            result = response["result"]
            assert result["analysis_id"]
            assert result["job_id"]
            done = wait_job(bp, result["job_id"], timeout=300)
            assert done["status"] == "COMPLETED", done
            return done["result"]["analysis_id"]

        invalid_pca = bp.request(sequence, "analysis.pca", {})
        sequence += 1
        assert invalid_pca["error"]["code"] == "INVALID_PARAMS"

        pca_response = bp.request(sequence, "analysis.pca", {"run_id": run_id, "mode": "structure", "preprocess": "center"})
        sequence += 1
        assert pca_response["result"]["job_id"]
        pca_done = wait_job(bp, pca_response["result"]["job_id"], timeout=300)
        assert pca_done["status"] == "COMPLETED", pca_done
        pca_cached = bp.request(sequence, "analysis.pca", {"run_id": run_id, "mode": "structure", "preprocess": "center"})
        sequence += 1
        assert pca_cached["result"]["job_id"] is None
        assert pca_cached["result"]["analysis_id"] == pca_response["result"]["analysis_id"]

        fps_response = bp.request(sequence, "analysis.fps", {"run_id": run_id, "n_samples": 4, "seed": 42})
        sequence += 1
        fps_done = wait_job(bp, fps_response["result"]["job_id"], timeout=300)
        assert fps_done["status"] == "COMPLETED", fps_done

        projection_ids = [
            run("analysis.umap", {"run_id": run_id, "n_neighbors": 3, "min_dist": 0.1, "seed": 42}),
            run("analysis.tsne", {"run_id": run_id, "perplexity": 3, "max_iter": 250, "seed": 42}),
        ]
        assert projection_ids
        run("analysis.neighbors", {"run_id": run_id, "k": 2})
        run("analysis.similarity", {"run_id": run_id, "k": 2, "query_index": 0})
        pairwise_id = run("analysis.pairwise", {"run_id": run_id, "max_samples": 8})

        for algorithm, params in (
            ("kmeans", {"n_clusters": 2}),
            ("dbscan", {"eps": 2.0, "min_samples": 2}),
            ("hdbscan", {"min_cluster_size": 2}),
            ("agglomerative", {"n_clusters": 2}),
        ):
            run("analysis.cluster", {"run_id": run_id, "algorithm": algorithm, **params, "seed": 42})
        for algorithm, params in (
            ("knn", {"k": 2}),
            ("lof", {"k": 3, "contamination": 0.25}),
            ("isolation_forest", {"contamination": 0.25, "n_estimators": 10}),
            ("mahalanobis", {"contamination": 0.25}),
        ):
            run("analysis.outlier", {"run_id": run_id, "algorithm": algorithm, **params, "seed": 42})
        for algorithm, params in (
            ("fps", {"n_samples": 4}),
            ("random", {"n_samples": 4}),
            ("stratified", {"n_samples": 4}),
            ("cluster_representative", {"n_samples": 4, "n_clusters": 2}),
            ("per_element", {"n_samples": 4, "mode": "atom"}),
        ):
            run("analysis.sampling", {"run_id": run_id, "algorithm": algorithm, **params, "seed": 42})

        run("analysis.coverage", {"reference_run_id": run_id, "query_run_id": run_id, "chunk_size": 3, "reference_chunk_size": 3})
        run("analysis.overlap", {"reference_run_id": run_id, "query_run_id": run_id})
        run(
            "analysis.acquisition",
            {"reference_run_id": run_id, "query_run_id": run_id, "n_samples": 4, "novelty_weight": 0.7},
        )
        uncertainty_id = run(
            "analysis.acquisition",
            {
                "reference_run_id": run_id,
                "query_run_id": run_id,
                "n_samples": 4,
                "acquisition_method": "uncertainty_diversity",
                "uncertainty_k": 4,
                "seed": 42,
            },
        )
        run("analysis.compare", {"left_run_id": run_id, "right_run_id": run_id})
        mantel_id = run(
            "analysis.mantel",
            {"left_run_id": run_id, "right_run_id": run_id, "method": "spearman", "permutations": 19, "max_samples": 8, "seed": 42},
        )
        quality_id = run("analysis.feature_variance", {"run_id": run_id, "top_k": 4})
        correlation_id = run("analysis.feature_correlation", {"run_id": run_id, "method": "spearman", "top_k": 4, "heatmap_features": 4})
        effective_id = run("analysis.effective_dimension", {"run_id": run_id})
        effective_cached = bp.request(sequence, "analysis.effective_dimension", {"run_id": run_id, "preprocess": "standardized"})
        sequence += 1
        assert effective_cached["result"]["job_id"] is None
        assert effective_cached["result"]["analysis_id"] == effective_id
        run(
            "analysis.property_correlation",
            {"run_id": run_id, "property": "energy_per_atom", "folds": 3, "top_k": 4},
        )
        local_id = run("analysis.local_diversity", {"run_id": run_id, "mode": "atom", "n_clusters": 3, "k": 3, "cutoff": 3.0})
        run("analysis.kernel", {"run_id": run_id, "kernel": "rbf", "max_samples": 8})
        trajectory_id = run("analysis.trajectory", {"run_id": run_id, "frame_start": 0, "frame_end": 7, "frame_step": 1})
        run("analysis.drift", {"reference_run_id": run_id, "query_run_id": run_id, "chunk_size": 3, "reference_chunk_size": 3})
        run("analysis.sensitivity", {"run_ids": [run_id, run_id]})
        perturbation_id = run(
            "analysis.perturbation_sensitivity",
            {"run_id": run_id, "perturbation": "jitter", "n_amplitudes": 2, "max_amplitude": 0.01, "max_structures": 2, "seed": 42},
        )
        # Strain is the other perturbation the panel offers, and the only one
        # that touches the cell: a job that merely completes proves the affine
        # map did not push the structure out of its own box.
        strain_id = run(
            "analysis.perturbation_sensitivity",
            {"run_id": run_id, "perturbation": "strain", "n_amplitudes": 3, "max_amplitude": 0.05, "max_structures": 2, "seed": 42},
        )

        listed = bp.request(sequence, "analysis.list", {"run_id": run_id})
        sequence += 1
        assert listed["result"]
        for analysis_id, kind in ((uncertainty_id, "acquisition"), (mantel_id, "mantel"), (local_id, "local_diversity"), (effective_id, "effective_dimension"), (trajectory_id, "trajectory"), (perturbation_id, "perturbation_sensitivity"), (strain_id, "perturbation_sensitivity")):
            checked = bp.request(sequence, "analysis.get", {"analysis_id": analysis_id})
            sequence += 1
            assert checked["result"]["status"] == "COMPLETED"
            assert checked["result"]["preview"]["kind"] == kind
            if kind == "effective_dimension":
                assert checked["result"]["preview"]["preprocess"] == "standardized"
                assert checked["result"]["preview"]["pca_basis"] == "correlation"
                assert checked["result"]["preview"]["pca_feature_count"] > 0
            if kind == "acquisition":
                # The cross-dataset algorithms state the scale they measured on:
                # the request omits `preprocess`, so the answer has to carry the
                # resolved default (deep review P1-14).
                assert checked["result"]["preview"]["preprocess"] == "standardized"
            if kind == "perturbation_sensitivity":
                # The response summarizes a sampled subset, so the artifact must
                # expose how many structures were available, not just how many
                # were swept.
                preview = checked["result"]["preview"]
                assert preview["sample_count"] == 2
                assert preview["available_structure_count"] == 8
                assert any("sampled 2 of 8 structures" in warning for warning in checked["result"]["warnings"])
            if kind == "trajectory":
                # The trajectory view derives thresholds live, so the artifact
                # must carry the robust statistics, the detection space and the
                # visual PCA variance next to the per-frame series.
                preview = checked["result"]["preview"]
                assert preview["event_method"] == "mad"
                assert preview["event_space"] == "descriptor"
                assert preview["pc1_explained_variance"] >= 0
                files = checked["result"]["artifact_manifest"]["files"]
                for name in ("coords", "pc_explained_variance", "step_distance", "reference_distance", "cumulative_distance", "sample_indices"):
                    assert name in files
        correlation = bp.request(sequence, "analysis.get", {"analysis_id": correlation_id})
        sequence += 1
        assert correlation["result"]["parameters"]["method"] == "spearman"
        assert correlation["result"]["preview"]["correlation_metric"] == "spearman"
        got = bp.request(sequence, "analysis.get", {"analysis_id": quality_id})
        sequence += 1
        assert got["result"]["status"] == "COMPLETED"
        preview = bp.request(sequence, "analysis.preview", {"analysis_id": quality_id, "limit": 20})
        sequence += 1
        assert preview["result"]["kind"] == "feature_variance"
        chunk = bp.request(sequence, "analysis.chunk", {"analysis_id": quality_id, "array": "variance", "limit": 4})
        sequence += 1
        assert len(chunk["result"]["data"]) == 4
        pairwise = bp.request(
            sequence,
            "analysis.chunk",
            {"analysis_id": pairwise_id, "array": "distance_matrix", "limit": 8, "column_end": 8},
        )
        sequence += 1
        assert pairwise["result"]["shape"] == [8, 8]
        assert len(pairwise["result"]["data"]) == 8

        # Every array a result panel fetches has to exist in the artifact it
        # fetches it from: frontend/src/features/analysis/registry.ts
        # ARTIFACT_ARRAYS drives the request list and the panel's loading gate, so
        # a renamed or optimistic name means that panel waits for a payload that
        # never arrives - and no other test here would notice.
        wanted = artifact_arrays_by_kind()
        complete: set[str] = set()
        for row in listed["result"]:
            detail = bp.request(sequence, "analysis.get", {"analysis_id": row["id"]})
            sequence += 1
            result = detail["result"]
            files = (result.get("artifact_manifest") or {}).get("files") or []
            kind = str((result.get("preview") or {}).get("kind"))
            if all(name in files for name in wanted.get(kind, [])):
                complete.add(kind)
        # A kind can be produced by several algorithms and only one of them
        # publishes the array (the FPS coverage curves), so the claim is that
        # every kind the panel knows about has *some* result that does.
        assert set(wanted) <= complete, sorted(set(wanted) - complete)

        export_path = tmp_path / "analysis-subset.json"
        export_id = run("analysis.export", {"run_id": run_id, "indices": [0, 2], "mode": "structure", "format": "json", "output_path": str(export_path)})
        assert export_id
        assert export_path.is_file()

        deleted = bp.request(sequence, "analysis.delete", {"analysis_id": quality_id})
        assert deleted["result"]["ok"] is True
        missing = bp.request(sequence + 1, "analysis.get", {"analysis_id": quality_id})
        assert missing["error"]["code"] == "ANALYSIS_NOT_FOUND"
    finally:
        assert bp.close() == 0
