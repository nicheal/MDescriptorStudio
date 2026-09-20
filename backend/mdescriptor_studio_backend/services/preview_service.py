"""Shaping the bounded identity/plot preview stored with each analysis."""

from __future__ import annotations

import numpy as np

from ..analysis import DescriptorMatrix
from .analysis_helpers import _MAX_PREVIEW_POINTS, _PREVIEW_ARRAY_KEYS


class AnalysisPreviewMixin:
    """Preview/point shaping for stored analysis artifacts."""

    def _build_preview(self, result: dict, samples: DescriptorMatrix, analysis_type: str, reference_samples: DescriptorMatrix | None = None) -> dict:
        arrays = result.get("arrays", {})
        preview = dict(result.get("preview") or {})

        def sample_identity(index: int) -> dict | None:
            if index < 0 or index >= samples.n_samples:
                return None
            item = {"i": index, "frame": int(samples.frame[index]), "sample_id": samples.sample_ids[index]}
            if samples.row is not None:
                item["row"] = int(samples.row[index])
            if samples.elements is not None and index < len(samples.elements):
                item["element"] = int(samples.elements[index])
            return item

        def reference_identity(index: int) -> dict | None:
            if reference_samples is None or index < 0 or index >= reference_samples.n_samples:
                return None
            item = {
                "reference_i": index,
                "reference_frame": int(reference_samples.frame[index]),
                "reference_sample_id": reference_samples.sample_ids[index],
            }
            if reference_samples.row is not None:
                item["reference_row"] = int(reference_samples.row[index])
            return item

        if "coords" in arrays:
            coords = np.asarray(arrays["coords"])
            count = min(coords.shape[0], samples.n_samples, _MAX_PREVIEW_POINTS)
            # Past the cap the preview strides over the whole set instead of
            # taking its beginning, so a capped result still shows the extent of
            # what it describes.  The result table reads this same list.
            indices = np.linspace(0, coords.shape[0] - 1, count, dtype=np.int64) if coords.shape[0] > count else np.arange(coords.shape[0])
            sample_indices = np.asarray(arrays.get("sample_indices", []), dtype=np.int64)
            # Convert each column once, outside the loop. This used to call
            # np.asarray on every one of the nine preview keys for every one of up
            # to 20 000 points - twice per key, once for .ndim and once for len() -
            # and then read the result back scalar by scalar, which is the slowest
            # way to walk an array. Measured on 20 000 points x 9 columns: 83 ms,
            # of which the .ndim checks alone were 19 ms. A column that is a
            # Python list would also have been re-materialised per point.
            columns: dict[str, tuple[list, bool]] = {}
            for key in _PREVIEW_ARRAY_KEYS:
                if key not in arrays:
                    continue
                converted = np.asarray(arrays[key])
                if converted.ndim != 1:
                    continue
                output_key = "label" if key == "labels" else "cluster" if key == "cluster_labels" else "element" if key == "elements" else key
                integral = key in ("labels", "cluster_labels", "elements", "coordination")
                # .tolist() gives Python scalars, so the loop indexes a list
                # instead of boxing a numpy scalar per point.
                columns[output_key] = (converted.tolist(), integral)
            points = []
            for i in indices.tolist():
                logical_index = int(sample_indices[i]) if sample_indices.ndim == 1 and i < sample_indices.size else int(i)
                point = sample_identity(logical_index)
                if point is None:
                    continue
                point.update({"x": float(coords[i, 0]), "y": float(coords[i, 1])})
                for output_key, (values, integral) in columns.items():
                    if i < len(values):
                        point[output_key] = int(values[i]) if integral else float(values[i])
                points.append(point)
            preview["points"] = points
            preview["total_points"] = int(coords.shape[0])
            # One list serves the scatter and the result table. This branch used
            # to build a second list over the same `indices` with the arrays'
            # plural names (`labels`, `cluster_labels`) so the table could use a
            # column header per key - 1.8 MB of the 3.9 MB a 20k-point cluster
            # preview cost, and an invariant (`tests/test_analysis_api.py`) whose
            # only job was to keep the two halves sampling the same samples.
            if "selected_indices" in arrays:
                selected = np.asarray(arrays["selected_indices"], dtype=np.int64)
                preview["selected"] = [item for i in selected[:_MAX_PREVIEW_POINTS].tolist() if (item := sample_identity(int(i))) is not None]
        elif analysis_type == "pairwise" and "sample_indices" in arrays:
            matrix_indices = np.asarray(arrays["sample_indices"], dtype=np.int64)
            preview["matrix_samples"] = [item for i in matrix_indices[:_MAX_PREVIEW_POINTS].tolist() if (item := sample_identity(int(i))) is not None]
        elif "selected_indices" in arrays:
            selected = np.asarray(arrays["selected_indices"], dtype=np.int64)
            preview["selected"] = [item for i in selected[:_MAX_PREVIEW_POINTS].tolist() if (item := sample_identity(int(i))) is not None]
        elif analysis_type in ("similarity", "neighbors") and "indices" in arrays:
            indices = np.asarray(arrays["indices"], dtype=np.int64)
            distances = np.asarray(arrays.get("distances", []))
            similarities = np.asarray(arrays.get("similarity", []))
            rows = []
            if indices.ndim == 1:
                for rank, neighbor in enumerate(indices.tolist()):
                    item = sample_identity(int(neighbor))
                    if item is None:
                        continue
                    item["rank"] = rank + 1
                    if rank < distances.size:
                        item["distance"] = float(distances[rank])
                    if rank < similarities.size:
                        item["similarity"] = float(similarities[rank])
                    rows.append(item)
            elif indices.ndim == 2:
                for source_index in range(indices.shape[0]):
                    for rank, neighbor in enumerate(indices[source_index].tolist()):
                        if len(rows) >= _MAX_PREVIEW_POINTS:
                            break
                        item = sample_identity(int(neighbor))
                        source = sample_identity(source_index)
                        if item is None or source is None:
                            continue
                        item.update({"source_i": source_index, "source_frame": source["frame"], "rank": rank + 1})
                        if distances.ndim == 2 and rank < distances.shape[1]:
                            item["distance"] = float(distances[source_index, rank])
                        rows.append(item)
                    if len(rows) >= _MAX_PREVIEW_POINTS:
                        break
            preview["rows"] = rows
            preview["total_rows"] = int(indices.size if indices.ndim == 1 else indices.shape[0] * indices.shape[1])
        elif "labels" in arrays or "scores" in arrays or "distances" in arrays or "coordination" in arrays or "uncertainty" in arrays:
            lengths = [len(np.asarray(arrays[key])) for key in _PREVIEW_ARRAY_KEYS if key in arrays and np.asarray(arrays[key]).ndim == 1]
            n = min([samples.n_samples, *lengths]) if lengths else samples.n_samples
            count = min(n, _MAX_PREVIEW_POINTS)
            rows = []
            for i in range(count):
                item = sample_identity(i)
                if item is None:
                    continue
                for key in _PREVIEW_ARRAY_KEYS:
                    if key in arrays and i < len(arrays[key]):
                        value = np.asarray(arrays[key])[i]
                        if np.asarray(value).ndim == 0:
                            output_key = "element" if key == "elements" else key
                            item[output_key] = int(value) if key in ("labels", "elements", "coordination") else float(value)
                        else:
                            item[key] = self._json_safe(value)
                if "nearest_indices" in arrays and i < len(arrays["nearest_indices"]):
                    nearest = reference_identity(int(np.asarray(arrays["nearest_indices"])[i]))
                    if nearest:
                        item.update(nearest)
                rows.append(item)
            preview["rows"] = rows
            preview["total_rows"] = n
        if analysis_type == "feature_variance" and result.get("warnings"):
            preview["warnings"] = list(result["warnings"])
        return self._json_safe(preview)


__all__ = ["AnalysisPreviewMixin"]
