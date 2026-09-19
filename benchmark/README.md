# Benchmark

`run_benchmarks.py` measures the costs the paper's evaluation section needs:

```
.venv/Scripts/python.exe benchmark/run_benchmarks.py            # full
.venv/Scripts/python.exe benchmark/run_benchmarks.py --quick     # smallest sizes
.venv/Scripts/python.exe benchmark/run_benchmarks.py --suite analysis
```

| Suite | What it holds still | Why a reviewer asks |
| --- | --- | --- |
| `descriptor` | ACSF over seeded displaced diamond cells, 256 and 2048 structures, then the same batch at 1 / 2 / all-core thread counts | engine throughput and how well it uses cores |
| `analysis` | PCA, the RBF kernel eigenspectrum and UMAP at 500 / 2 000 / 8 000 samples | which analyses are quadratic in sample count, and how steeply |
| `storage` | `values.npy` written and read back at 20 000 × 512, float32 and float64 | the cost of the artifact format on the result path |

Each row reports median wall time over three runs, the min/max spread, a
throughput and the process working set. Results are written to
`results/<utc>/results.json` (gitignored) next to the `values_*.npy` the storage
suite produces, so a run can be re-read or diffed later.

## What this is not

**Not a CI gate.** Wall-clock numbers on shared runners vary by an order of
magnitude, and the register-time budget in `.github/workflows/ci.yml` already
had to stop measuring a cold-start contention cost it could not control
(commit `8e3e06b`). CI runs `--quick --suite analysis` only to prove the
harness still works; the numbers are read, not asserted.

**GPU scaling is not measured.** No CUDA runner exists here, and a number
taken from one machine is not a property of the software. The engine's
device selection is covered by the descriptor-service tests instead.

## Reading the memory column

`rss_high_water_mb` is the process working set after the run, and
`peak_rss_mb` its growth during the measured calls. On Windows that is the
current working set, sampled around the call. On Linux and macOS the OS only
offers `ru_maxrss`, a *lifetime* high-water mark for the whole process, so
there the column includes interpreter and import costs and reads as an upper
bound - the same caveat that applies to `memory_peak_bytes` on a descriptor
run. See `services/descriptor_service.py::_process_rss_bytes`.
