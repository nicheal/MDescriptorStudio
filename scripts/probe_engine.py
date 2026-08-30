"""Dump the installed mdescriptor engine API surface as JSON.

Usage:
    .venv/Scripts/python.exe scripts/probe_engine.py [--out FILE]

Re-run whenever the mdescriptor version pin changes and diff the JSON
against docs/plan/engine-api-report.json to spot schema drift.
Deliberately read-only: never instantiates a descriptor or loads models.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import mdescriptor as md

SYMBOLS = [
    "StructureBatch",
    "Descriptor",
    "DescriptorResult",
    "DescriptorRegistry",
    "DescriptorConfiguration",
    "ExecutionOptions",
    "OutputOptions",
    "ComputeControl",
    "describe_descriptor",
    "get_runtime_info",
    "list_descriptors",
    "create_descriptor",
    "gui_baseline",
    "preload_native",
    "UnsupportedPeriodicityError",
]


def main() -> int:
    descriptors = sorted(md.list_descriptors())
    capabilities: set[str] = set()
    parameter_types: dict[str, int] = {}
    schemas: dict[str, dict] = {}
    parameter_total = 0
    parameter_display_names = 0
    parameter_descriptions = 0
    nested_parameter_total = 0
    nested_parameter_display_names = 0
    nested_parameter_descriptions = 0

    for name in descriptors:
        schema = md.describe_descriptor(name)
        schemas[name] = schema
        capabilities.update(schema.get("capabilities", []))
        for param in schema["parameters"].values():
            parameter_total += 1
            parameter_display_names += bool(param.get("display_name"))
            parameter_descriptions += bool(param.get("description"))
            t = param.get("type")
            parameter_types[t] = parameter_types.get(t, 0) + 1
            for nested in (param.get("properties") or {}).values():
                nested_parameter_total += 1
                nested_parameter_display_names += bool(nested.get("display_name"))
                nested_parameter_descriptions += bool(nested.get("description"))

    detailed = md.list_descriptors(detailed=True)

    report = {
        "engine_version": md.__version__,
        "runtime_info": dict(md.get_runtime_info()),
        "descriptor_names": descriptors,
        "descriptor_count": len(descriptors),
        "capabilities_observed": sorted(capabilities),
        "parameter_types": dict(sorted(parameter_types.items())),
        "parameter_presentation": {
            "top_level_total": parameter_total,
            "top_level_display_names": parameter_display_names,
            "top_level_descriptions": parameter_descriptions,
            "nested_total": nested_parameter_total,
            "nested_display_names": nested_parameter_display_names,
            "nested_descriptions": nested_parameter_descriptions,
        },
        "detailed_descriptor_count": len(detailed),
        "structure_batch_from_frames": hasattr(md.StructureBatch, "from_frames"),
        "descriptors": schemas,
        "error_types": sorted(x for x in dir(md) if x.endswith("Error")),
        "compute_control_methods": sorted(
            x for x in dir(md.ComputeControl()) if not x.startswith("_")
        ),
        "symbols_present": {s: hasattr(md, s) for s in SYMBOLS},
    }

    text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if "--out" in sys.argv:
        out = Path(sys.argv[sys.argv.index("--out") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
