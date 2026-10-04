# F — gen-5 freeze re-benchmark: preregistrations launched (2026-10-04)

Milestone F of docs/reviews/2026-10-02-improvement-plan.md. Both sweeps RUNNING
in parallel (machine headroom measured 2026-10-02: each sweep averages 5–12 of
32 cores):

- **carbon**: `benchmark/config.gen5-carbon.json` → results dir `20261004T031301Z`,
  console `benchmark/results/gen5_carbon_console.log`.
- **PdCuNiP**: `benchmark/config.gen5-pdcunip.json` → results dir `20261004T031310Z`,
  console `benchmark/results/gen5_pdcunip_console.log`.

## Scenario

Same frozen R4 design per material (20 repeats × 7 groups × 10k evaluations,
structure_fps_v1, novelty threshold 0.25, anchors carbon 1322/5075 · PdCuNiP
2256/2133, geometry precheck on), re-registered under
`GENERATION_ALGORITHM_VERSION = "gen-5"`, `harness_min_version = 2026-10-03`
(harness bumped: the metric whitelist, summary and per-run console line now
carry the gen-5 calibers).

## Metric contract

- **Primary**: `strict_unique_v2_per_100_evals` — the permutation-invariant
  canonical-greedy count (candidates by stable id, atom rows lexicographic).
- **Secondaries**: BOTH calibers per sweep — the gen-4 visit-order
  `unique_per_100_evals`, the post-screening `archived_unique_per_100_evals`
  (equals the primary rate on these runs: no screening configured — the
  fields still flow, and screening preregistrations get the split for free),
  plus coverage/accepted/wall/rss.
- Cross-material value comparisons remain out of scope (2026-10-03 migration
  record); the primary comparison is paired per-seed differences WITHIN each
  material, with the gen-4-era sweeps (20261002T010733Z / 20261002T005922Z)
  as the same-code pre-gen-5 reference — engine-side trajectories are
  bit-identical to gen-4 for these unscreened runs, so the gen-5 sweep also
  re-validates reproducibility against the archived baseline.

## 分析契约（sweep 完成后）

1. 配对逐 seed 差值（组内、同口径）：v2 主指标排名 vs gen-4 口径排名。
2. gen-5 sweep 与同代码 gen-4 存档 sweep 的逐 seed 一致性抽查（确定性互证：
   非局部运行轨迹逐位不变 → unique/v2 应与 20261002T010733Z / 20261002T005922Z
   完全一致——这是对 gen-5 "仅增量字段" 声明的最终审计）。
3. 报告 pre-screen 与 archived 两套发现率（本两 sweep 无筛选 → 二者相等；
   契约与管线就绪，筛选预注册可直接复用）。
4. 发布包：`benchmark/published/2026-10-xx-f-gen5/`（jsonl + summary +
   environment + SHA256SUMS，`.gitattributes` 保证字节一致），README 附
   数据集获取方式（carbon 6738 帧 / PdCuNiP 9615 帧来源与注册流程），关闭
   L5 独立复现行。

## 状态

- F1–F4 完成（harness 指标 + 配置 + 测试 + 启动）；F6（分析 + 发布 + L5）等
  sweep 完成通知。预计 wall：carbon ~1.5 天、PdCuNiP ~2 天（并行争用下略长）。
