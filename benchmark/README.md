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
| `storage` | `values.npy` written and read back at 20 000 × 512, float32 and float64 | the cost of the artifact format on the result path. **Cache-warm**: the read row re-opens a file the same run just wrote, so its MB/s is page-cache speed, not device throughput |

Each row reports median wall time over three runs, the min/max spread, a
throughput and the process working set, plus a `cache` field saying what each
throughput number was actually measured against. Results are written to
`results/<utc>/results.json` (gitignored) next to the `values_*.npy` the storage
suite produces, so a run can be re-read or diffed later.

**Do not cite the storage rows as I/O bandwidth.** They are cache-warm by
construction - measured at 4 452 and 4 476 MB/s read against 265-300 MB/s write
on the same file - and a cold-device number would need a privileged handle on
Windows, which is not what this suite is for.

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
`rss_growth_mb` its growth during the measured calls. On Windows that is the
current working set, sampled around the call. On Linux and macOS the OS only
offers `ru_maxrss`, a *lifetime* high-water mark for the whole process, so
there the column includes interpreter and import costs and reads as an upper
bound - the same caveat that applies to `memory_peak_bytes` on a descriptor
run. See `services/descriptor_service.py::_process_rss_bytes`.

Every row carries `repeats`, and `spread_seconds` is null where a case ran
once: the large analysis size classes are single-shot measurements, and an
interval of `[t, t]` would read like a spread. `results.json` records
`omp_num_threads` for the run - the harness sets it *before* importing numpy,
because BLAS chooses its threads when the library loads and a later value is
ignored.

## Scientific comparison: genetic_vs_random.py

`genetic_vs_random.py` (G4-2) is a different animal from `run_benchmarks.py`:
it measures search *efficacy*, not cost. For each repeat seed it runs the
generation engine twice-plus over the local `carbon` dataset with the NEP
descriptor run, everything held identical except the optimizer
(`random`, `random-reuse` = Random with accepted-seed reuse, `genetic`).
Core metrics: unique novel environments per 100 descriptor evaluations
(lower duplicate waste and wider discovery win) and the final accepted-only
coverage radius. It replicates the generation worker's assembly against the
app data read-only — no DB rows, no job queue — and checkpoints
`results/<utc>/genetic_vs_random.json` after every run.

This is not a CI gate either: it needs the local dataset/descriptor-run ids
pinned in the script and takes hours at the full 20 × 10 000-evaluation
sweep. `--pilot` runs one 400-evaluation repeat of each optimizer as a
smoke test. Wall-time columns here are informational only — the headline
metrics are budget-normalized discovery numbers, not speed.

`--optimizers target_region` adds the G5-2 targeted-resampling optimizer and
a second metric family: `anchor_proximity` (median / p90 / within_radius of
each accepted structure's distance to the anchor descriptors, in robust-scaled
units). `--anchor-frames` picks the anchor dataset frames — they must satisfy
the run's own geometry constraints (the worker refuses pathological anchors).
Discovery and proximity are deliberately different yardsticks: target_region
trades global discovery for accepted structures concentrated around the
anchors, so compare each optimizer on the metric it is aiming at.
