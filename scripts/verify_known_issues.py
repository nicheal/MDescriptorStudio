"""Re-verify docs/plan/engine-known-issues.md items against the installed engine.

Usage:
    .venv/Scripts/python.exe scripts/verify_known_issues.py            # all checks
    .venv/Scripts/python.exe scripts/verify_known_issues.py --deadlock-child
                                                                       # internal: child for issue #1

Prints one "## issue N ... VERDICT" section per known issue plus a final JSON summary.
"""
from __future__ import annotations

import json
import sys
import threading
import time
import traceback
from pathlib import Path

import numpy as np

VERDICTS: dict[str, dict] = {}


def record(issue: str, verdict: str, detail: dict) -> None:
    VERDICTS[issue] = {"verdict": verdict, **detail}
    print(f"  => {verdict}: {json.dumps(detail, ensure_ascii=False, default=str)[:500]}")


def fill_required(schema: dict) -> dict:
    """Build a minimal parameter dict from a descriptor schema (recursive)."""
    params: dict = {}
    for key, meta in schema.get("parameters", {}).items():
        if not meta.get("required"):
            continue
        params[key] = _fill_value(meta)
    return params


def _fill_value(meta: dict):
    ptype = meta.get("type")
    if ptype == "species":
        return [1]
    if ptype == "integer":
        return int(meta.get("default") if meta.get("default") is not None else 1)
    if ptype == "number":
        return float(meta.get("default") if meta.get("default") is not None else 1.0)
    if ptype == "boolean":
        return bool(meta.get("default", False))
    if ptype == "enum":
        return (meta.get("enum") or [""])[0]
    if ptype == "array":
        return meta.get("default") or []
    if ptype == "object":
        props = meta.get("properties") or {}
        return {k: _fill_value(m) for k, m in props.items() if m.get("required") or m.get("default") is not None}
    if ptype == "model":
        return None  # bundled resource auto-resolves
    return meta.get("default")


def batches():
    import mdescriptor as md

    h2_numbers = np.array([1, 1], dtype=np.int64)
    h2_pos = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.74]])
    isolated = md.StructureBatch(
        numbers=h2_numbers,
        positions=h2_pos,
        cells=np.zeros((1, 3, 3)),
        pbc=np.zeros((1, 3), dtype=bool),
        offsets=np.array([0, 2]),
        ids=("h2",),
    )
    a = 5.43
    si_cell = np.diag([a, a, a]).reshape(1, 3, 3)
    si_pos = np.array([[0.0, 0.0, 0.0], [a / 4, a / 4, a / 4]])
    periodic = md.StructureBatch(
        numbers=np.array([14, 14], dtype=np.int64),
        positions=si_pos,
        cells=si_cell,
        pbc=np.ones((1, 3), dtype=bool),
        offsets=np.array([0, 2]),
        ids=("si2",),
    )
    # mixed: isolated H2 + periodic Si2 in one batch
    mixed = md.StructureBatch(
        numbers=np.concatenate([h2_numbers, np.array([14, 14], dtype=np.int64)]),
        positions=np.concatenate([h2_pos, si_pos]),
        cells=np.concatenate([np.zeros((1, 3, 3)), si_cell]),
        pbc=np.concatenate([np.zeros((1, 3), dtype=bool), np.ones((1, 3), dtype=bool)]),
        offsets=np.array([0, 2, 4]),
        ids=("h2", "si2"),
    )
    return isolated, periodic, mixed


def build(name: str, params: dict | None = None):
    import mdescriptor as md

    schema = md.describe_descriptor(name)
    if params is None:
        params = fill_required(schema)
    params = {k: v for k, v in params.items() if v is not None}
    cfg = md.DescriptorConfiguration(
        schema_version=md.CONFIGURATION_SCHEMA_VERSION,
        descriptor=name,
        parameters=params,
    )
    return md.create_descriptor(cfg)


# ---------------------------------------------------------------- issue #1
def deadlock_child() -> int:
    """Worker thread builds ACE while another thread blocks on stdin forever."""
    import mdescriptor as md

    stop = threading.Event()

    def blocker():
        try:
            sys.stdin.buffer.read()  # blocks until parent closes pipe
        except Exception:
            pass
        stop.set()

    t = threading.Thread(target=blocker, daemon=True)
    t.start()
    time.sleep(0.3)  # ensure the reader thread is parked on stdin
    t0 = time.monotonic()
    try:
        isolated, _, _ = batches()
        desc = build("ACE")
        isolated_num = md.StructureBatch(
            numbers=np.array([1, 1], dtype=np.int64),
            positions=np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.74]]),
            cells=np.zeros((1, 3, 3)),
            pbc=np.zeros((1, 3), dtype=bool),
            offsets=np.array([0, 2]),
            ids=("h2",),
        )
        desc.compute(isolated_num)
        elapsed = time.monotonic() - t0
        print(f"BUILD_DONE elapsed={elapsed:.2f}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"BUILD_FAILED elapsed={time.monotonic() - t0:.2f} err={exc!r}", flush=True)
    print("CHILD_END", flush=True)
    return 0


def check_deadlock() -> None:
    import subprocess

    print("## issue 1: lazy-import deadlock (create_descriptor while stdin blocked)")
    child = subprocess.Popen(
        [sys.executable, Path(__file__).resolve(), "--deadlock-child"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    # Deliberately keep child's stdin open for the whole run.
    lines: list[str] = []
    t0 = time.monotonic()
    try:
        deadline = 45.0
        while True:
            line = child.stdout.readline()
            if not line:
                break
            lines.append(line.rstrip())
            if "CHILD_END" in line:
                break
            if time.monotonic() - t0 > deadline:
                lines.append("TIMEOUT_PARENT")
                break
    finally:
        if child.poll() is None:
            child.kill()
        try:
            child.stdin.close()
        except Exception:
            pass
        child.wait(timeout=10)
    elapsed = time.monotonic() - t0
    done = [ln for ln in lines if ln.startswith("BUILD_DONE")]
    print("\n".join(lines[-6:]))
    if done and elapsed < 30:
        record("1", "FIXED?", {"elapsed_s": round(elapsed, 1), "note": "build completed while stdin still open"})
    elif "BUILD_DONE" in "".join(lines):
        record("1", "SUSPECT", {"elapsed_s": round(elapsed, 1), "note": "completed but slow"})
    else:
        record("1", "STILL_PRESENT", {"output": lines[-6:], "elapsed_s": round(elapsed, 1)})


# ---------------------------------------------------------------- issue #2
def check_backend_annotation() -> None:
    print("## issue 2: backend annotation vs actual execution path")
    import mdescriptor as md

    rows = {}
    for name in md.list_descriptors():
        s = md.describe_descriptor(name)
        rows[name] = {
            "backend": s.get("backend"),
            "execution_engine": s.get("execution_engine"),
        }
    engines = sorted({r["execution_engine"] for r in rows.values()})
    backends = sorted({r["backend"] for r in rows.values()})
    print(f"  backend values: {backends}; execution_engine values: {engines}")
    print(f"  DPA4: {rows['DPA4']}")
    record(
        "2",
        "FIXED?" if all(r["execution_engine"] for r in rows.values()) else "STILL_PRESENT",
        {"dpa4": rows["DPA4"], "execution_engine_values": engines},
    )


# ---------------------------------------------------------------- issue #3
def check_cooperative_cancel() -> None:
    print("## issue 3: DPA4/DPA4C cooperative cancel")
    import mdescriptor as md

    schema_report = {}
    for name in ("DPA4", "DPA4C"):
        s = md.describe_descriptor(name)
        schema_report[name] = {
            "cooperative_cancel": s["execution"].get("cooperative_cancel"),
            "in_capabilities": "cooperative_cancel" in s.get("capabilities", []),
        }
    print(f"  schema: {schema_report}")

    # functional: build DPA4, cancel mid-compute
    func = None
    try:
        t0 = time.monotonic()
        desc = build("DPA4")
        build_s = time.monotonic() - t0
        cls = type(desc).__name__
        native = [x for x in dir(desc) if "native" in x.lower() or "Calculator" in x]
        print(f"  DPA4 built in {build_s:.1f}s, class={cls}, native attrs={native}")

        # 4 frames x 3x3x3 conventional Si supercell (216 atoms each)
        a = 5.43
        basis = np.array([
            [0, 0, 0], [0.5, 0.5, 0], [0.5, 0, 0.5], [0, 0.5, 0.5],
            [0.25, 0.25, 0.25], [0.75, 0.75, 0.25], [0.75, 0.25, 0.75], [0.25, 0.75, 0.75],
        ]) * a
        nrep = 3
        one = []
        for i in range(nrep):
            for j in range(nrep):
                for k in range(nrep):
                    one.append(basis + np.array([i, j, k]) * a)
        one = np.vstack(one)  # 216 x 3
        numbers = np.tile(np.array(8 * [14]), nrep**3).astype(np.int64)
        n_atoms = one.shape[0]
        n_frames = 4
        positions = np.stack([one + f * 1e-3 for f in range(n_frames)]).reshape(-1, 3)
        cells = np.tile(np.diag([nrep * a] * 3).reshape(1, 3, 3), (n_frames, 1, 1))
        batch = md.StructureBatch(
            numbers=np.tile(numbers, n_frames),
            positions=positions,
            cells=cells,
            pbc=np.ones((n_frames, 3), dtype=bool),
            offsets=np.arange(n_frames + 1) * n_atoms,
            ids=tuple(f"si{nrep}x{nrep}x{nrep}_{i}" for i in range(n_frames)),
        )
        print(f"  cancel batch: {n_frames} frames x {n_atoms} atoms")
        control = md.ComputeControl()
        result: dict = {}

        def compute():
            try:
                t = time.monotonic()
                desc.compute(batch, control=control)
                result["outcome"] = "completed"
            except Exception as exc:  # noqa: BLE001
                result["outcome"] = f"raised {type(exc).__name__}"
            result["elapsed_s"] = round(time.monotonic() - t, 2)

        th = threading.Thread(target=compute, daemon=True)
        t = time.monotonic()
        th.start()
        time.sleep(1.5)
        cancel_at = time.monotonic() - t
        control.cancel()
        th.join(timeout=300)
        func = {
            "class": cls,
            "cancelled_after_s": round(cancel_at, 1),
            "outcome": result.get("outcome"),
            "compute_elapsed_s": result.get("elapsed_s"),
            "control_cancelled": control.cancelled(),
        }
    except Exception as exc:  # noqa: BLE001
        func = {"error": repr(exc)}
    print(f"  functional: {func}")
    all_true = all(v["cooperative_cancel"] for v in schema_report.values())
    record("3", "FIXED?" if all_true else "STILL_PRESENT", {"schema": schema_report, "functional": func})


# ---------------------------------------------------------------- issue #4
def check_version_fields() -> None:
    print("## issue 4: per-descriptor version field")
    import mdescriptor as md

    missing = [
        n
        for n in md.list_descriptors()
        if not md.describe_descriptor(n).get("descriptor_version")
    ]
    sample = md.describe_descriptor("ACE").get("descriptor_version")
    print(f"  ACE descriptor_version={sample!r}; missing in {len(missing)} descriptors {missing}")
    record("4", "FIXED" if not missing else "PARTIAL", {"ace": sample, "missing": missing})


# ---------------------------------------------------------------- issue #5
def check_periodicity_errors() -> None:
    print("## issue 5: periodicity error messages (isolated input)")
    import mdescriptor as md

    isolated, _, _ = batches()
    out = {}
    for name in ("EwaldSumMatrix", "LMBTR", "MBTR", "SineMatrix", "ValleOganov", "LodeSphericalExpansion"):
        try:
            desc = build(name)
        except Exception as exc:  # noqa: BLE001
            out[name] = {"stage": "build", "type": type(exc).__name__, "msg": str(exc)[:200]}
            continue
        try:
            desc.compute(isolated)
            out[name] = {"stage": "compute", "type": "NO_ERROR", "msg": ""}
        except Exception as exc:  # noqa: BLE001
            out[name] = {"stage": "compute", "type": type(exc).__name__, "msg": str(exc)[:200]}
    for k, v in out.items():
        print(f"  {k}: [{v['type']}] {v['msg']}")
    mentions = sum(1 for v in out.values() if "periodic" in v["msg"].lower())
    record("5", "FIXED?" if mentions >= 6 else "STILL_PRESENT", {"mentions_periodic": mentions, "detail": out})


# ---------------------------------------------------------------- issue #6
def check_mixed_periodicity() -> None:
    print("## issue 6: mixed periodicity batch compute")
    import mdescriptor as md

    _, _, mixed = batches()
    results = {}
    for name in ("SOAP", "ACE", "SortedDistances"):
        try:
            desc = build(name, {"species": [1, 14]})
            res = desc.compute(mixed)
            n = None
            for attr in ("count", "n_frames", "size"):
                if hasattr(res, attr):
                    n = getattr(res, attr)
                    break
            results[name] = {"ok": True, "frames": str(n)}
        except Exception as exc:  # noqa: BLE001
            results[name] = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:150]}"}
        print(f"  {name}: {results[name]}")
    schema_mixed = {n: md.describe_descriptor(n)["input"]["mixed_periodicity"] for n in md.list_descriptors()}
    ok_count = sum(1 for v in schema_mixed.values() if v)
    all_ok = all(v["ok"] for v in results.values())
    record(
        "6",
        "FIXED?" if all_ok else "PARTIAL",
        {"functional": results, "schema_mixed_true": ok_count, "schema_mixed_false": sorted(n for n, v in schema_mixed.items() if not v)},
    )


# ---------------------------------------------------------------- issue #7 + #8
def check_docs_and_schema_contract() -> None:
    print("## issue 7: baseline doc shipped; issue 8: schema contract drift")
    import importlib.metadata
    import mdescriptor as md

    dist = importlib.metadata.distribution("MDescriptor")
    files = [str(f) for f in (dist.files or [])]
    docs = [f for f in files if f.lower().endswith((".md", ".rst", ".txt")) and "dist-info" not in f]
    baseline = [f for f in files if "gui-adaptation" in f.lower() or "baseline" in f.lower()]
    print(f"  md/rst docs in wheel: {docs}")
    print(f"  baseline doc files: {baseline}")
    record("7", "FIXED" if baseline else "STILL_PRESENT", {"baseline_files": baseline, "docs": docs[:10]})

    types: dict[str, int] = {}
    for name in md.list_descriptors():
        for p in md.describe_descriptor(name)["parameters"].values():
            types[p.get("type")] = types.get(p.get("type"), 0) + 1
    string_used = types.get("string", 0)
    print(f"  parameter types now: {types}")
    record(
        "8",
        "PARTIAL" if string_used == 0 else "FIXED",
        {"string_params": string_used, "type_counts": types},
    )


# ---------------------------------------------------------------- issue #9
def check_devices() -> None:
    print("## issue 9: devices")
    import mdescriptor as md

    devices = {}
    for name in md.list_descriptors():
        devices[name] = md.describe_descriptor(name)["execution"].get("devices")
    non_cpu = {k: v for k, v in devices.items() if v != ["cpu"]}
    print(f"  non-cpu devices: {non_cpu or 'none'}")
    record("9", "FIXED" if non_cpu else "STILL_PRESENT", {"non_cpu": non_cpu})


# ---------------------------------------------------------------- issue #10
def check_ergonomics() -> None:
    print("## issue 10: API ergonomics")
    import mdescriptor as md

    has_from_frames = hasattr(md.StructureBatch, "from_frames")
    ltype = type(md.list_descriptors())
    new_exc = [x for x in dir(md) if x.endswith("Error")]
    periodicity_exc = [x for x in new_exc if "periodic" in x.lower()]
    info = {
        "StructureBatch.from_frames": has_from_frames,
        "list_descriptors_returns": f"{ltype.__module__}.{ltype.__name__}",
        "error_types": new_exc,
        "periodicity_specific_error": periodicity_exc or None,
    }
    print(f"  {info}")
    verdict = "PARTIAL"
    if has_from_frames and periodicity_exc:
        verdict = "FIXED"
    record("10", verdict, info)


# ----------------------------------------------------------------
def main() -> int:
    if "--deadlock-child" in sys.argv:
        return deadlock_child()

    checks = [
        ("1", check_deadlock),
        ("2", check_backend_annotation),
        ("3", check_cooperative_cancel),
        ("4", check_version_fields),
        ("5", check_periodicity_errors),
        ("6", check_mixed_periodicity),
        ("7", check_docs_and_schema_contract),
        ("9", check_devices),
        ("10", check_ergonomics),
    ]
    import mdescriptor as md

    print(f"# verify_known_issues against mdescriptor {md.__version__}\n")
    for _, fn in checks:
        try:
            fn()
        except Exception:
            print(f"  !! checker crashed: {traceback.format_exc(limit=2)}")
        print()
    print("SUMMARY_JSON")
    print(json.dumps(VERDICTS, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
