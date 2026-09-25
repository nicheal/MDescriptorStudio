# 2026-09-25 — G3.5 Correctness Gate + Optimizer Lifecycle 完成记录

针对 2026-09-25 外部审阅(`nicheal/MDescriptorStudio@4dd00a3`)提出的 P0 问题与 G3.5 阶段建议,
本次变更完成 G3.5-1 Correctness、G3.5-2 Contract cleanup、G3.5-3 Optimizer lifecycle。
科学指标语义已改变,`GENERATION_ALGORITHM_VERSION` 升为 **gen-4**(gen-3 与 gen-4 的
coverage radius / coverage fitness / 停止条件不可比)。

## G3.5-1 Correctness(A01–A08 + A05)

| 编号 | 内容 | 落点 |
| --- | --- | --- |
| A01 | Coverage 空 archive 排序方向:fitness = `-R_cov({X})`,第一个 accept 选 max-min 中心 | `generation/objectives/coverage.py` |
| A02/A03 | 新增 `accepted_coverage_radius()`(accepted-only,首个 accept 前为 None,单调非增),Engine 每轮记录该值 | `generation/archive.py`、`generation/engine.py` |
| A04 | discovery-rate stop 按 `produces_novel_environment_count` capability 门控;local/composite 的 threshold 未配置时不产出 count | objectives + engine |
| A05 | `min_novel_per_100_evals=0.0` 不再被 `or 1.0` 吞掉 | `generation/models.py` |
| A06 | submit + worker 强制 `descriptor_run.dataset_id == request.dataset_id` | `services/generation_service.py` |
| A07 | submit 阶段 freshness 门控:run metadata 的 `dataset_fingerprint` 与 dataset 当前 fingerprint 比对,过期 → `ANALYSIS_STALE` | 同上 |
| A08 | local/composite 遇 structure-level run 在 submit 直接拒绝(worker 同分支为硬错误,删除假降级 warning) | 同上 |

## G3.5-2 Contract cleanup(A09–A12 + §25)

| 编号 | 内容 | 落点 |
| --- | --- | --- |
| A09 | `composition_locked` / `atom_count_locked` 单一科学默认 **locked=True**(dataclass / build_constraints / parse_request / catalog / UI store 五处一致);count-changing 算子需显式解锁(UI 启用即自动写 false) | constraints + models + registry + frontend store |
| A10 | optimizer 参数 submit 期完整校验:仅整数、1≤n_seeds≤1024、1≤children_per_seed≤1024、1≤batch_accept≤n_seeds×children_per_seed、未知键拒绝 | `generation/models.py` |
| A11 | displacement 语义分离:`max_sigma`(σ 上限,约束分布)与 `hard_cutoff`(硬上限,逐原子按范数缩到球面,方向保持);metadata `displacement_max` 改为真实位移范数 `max_i ||Δr_i||`;submit 期校验两者 | `operators/displacement.py` + frontend |
| A12 | 每轮新增 `unique_novel_environments`:batch selection 后按选择序对 frozen archive + 本轮已计数环境做 greedy union dedup;raw 与 unique 同时记录 | `generation/engine.py` + preview + UI |
| §25 | Optimizer 选择器由 `generation.catalog` 驱动;删除 TS 中的 roadmap 词汇(`genetic`/`pso`/`external`/`target_region`) | frontend types/labels/config |

## G3.5-3 Optimizer lifecycle

新契约(`generation/optimizers/base.py`):

```text
initialize(OptimizationContext) → propose(budget, rng) → ProposalBatch
                                ← observe(ObservationBatch)  ← 每轮全部提案的结果
                                ← state_dict()
```

* `generation/optimization/`:`OptimizationContext`、`ProposalBatch`、`CandidateObservation`
  (fitness / novelty / local_diversity / coverage_gain / novel_environment_count /
  geometry_rejection / accepted / selection_rank / scaled descriptor)、`ObservationBatch`。
* Engine 不再维护 feedback pool——accepted 候选经 `observe()` 折叠进 optimizer 自身状态,
  Random 以 `selection_rank` 保持插入序,FPS 剪枝结果与重构前逐位一致。
* Geometry-rejected / budget 截断的提案也进入观测(截断者除外,它们未进入管线)。
* 回归基线:重构前的 Random accepted 签名(3 个 seed 的 novelty run + feedback 路径 +
  discovery-stop 场景)固化为 golden 测试 `tests/test_generation_random_baseline.py`
  (fixture `tests/data/generation_random_baseline.json`),G4 期间任何破坏 Random 行为的
  改动都会被捕获。

## 尚未开始(按审阅顺序)

* G4-1 GA mutation-only(genome = displacement σ / strain / shear 连续基因 + operator mask)
* G4-2 Random vs GA benchmark(同 seed、同预算 10000 descriptor evaluations、20 repeats、
  以 `unique_novel_environments / 100 eval` 与 accepted-only coverage radius 为核心指标)
* G5 PSO / target-region / External adapter

## 验证

* 后端:`pytest tests` — 505 passed(5 个失败为环境缺可选依赖 hdbscan / 原生适配器上报,
  在干净 HEAD worktree 上复现确认与本次变更无关)。
* 前端:`tsc -b`、`eslint`、`vitest run` 225 passed。
