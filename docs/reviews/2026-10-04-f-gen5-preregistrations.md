# F — gen-5 freeze re-benchmark: preregistrations launched (2026-10-04)

Milestone F of docs/reviews/2026-10-02-improvement-plan.md. Both sweeps RUNNING
in parallel (machine headroom measured 2026-10-02: each sweep averages 5–12 of
32 cores):

- **carbon**: `benchmark/config.gen5-carbon.json` → results dir `20261004T031301Z`,
  console `benchmark/results/gen5_carbon_console.log`.
- **PdCuNiP**: `benchmark/config.gen5-pdcunip.json` → results dir `20261004T031310Z`
  (console 流已被下述同秒重复启动覆盖,活性与结果一律以 run_results.jsonl 为准)。

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

## Carbon 结果(2026-10-05 完成 140/140,分析同日)

sweep `20261004T031301Z` 于 10-05 07:53 收尾。完整性:7 组 × 20 seeds 全部
`max_evaluations` 收尾、无重复 (optimizer, seed) 行、structure_fps_v1、v2 列
齐全;SHA256SUMS 逐文件校验通过(清单文件本身为 CRLF 行尾,校验需
`tr -d '\r'` — 与 R4 发布包同类 nit,harness 后续可改为写 LF)。

**bit-exact 审计(契约第 2 条)——通过。** 与同代码 gen-4 存档 sweep
`20261002T010733Z`(carbon fps re-baseline)140 对 (optimizer, seed) 逐行比对:

- 行级 `unique_novel_environments` / `unique_per_100_evals` / `accepted` /
  `final_coverage_radius` / `evaluations` / `stopped_by` / `anchor_proximity` /
  `targeting_enabled` 全部逐位相同;aggregate 上 random v1 = 70.00 与存档值吻合。
- 逐轮记录共享字段子集(含每轮 best_fitness、rejected_* 分布、proposed/accepted)
  逐位相同、轮数相同;gen-5 侧仅**新增**逐轮字段(`strict_unique_v2`、
  `archived_strict_unique_v2`、`screening_passed/unscreenable/train_ready`)。
- 配置除指标声明(primary/secondary_metrics)外逐键一致。
- 结论:gen-5 "仅增量、不改轨迹" 的声明在真实数据上成立。

**口径分化(契约第 1/3 条):**

- pre-screen == archived 在全部 140 行成立(v1、raw 两套口径各自相等;本
  sweep 无筛选,符合预期,筛选预注册可直接复用该管线)。
- v1 ≠ v2 仅 **6/140 行**,|Δ| ≤ 0.02/100(≤2 个环境),双向:v2 高 2 行
  (genetic/1000 13.71→13.72、random/1008 70.00→70.01),低 4 行(pso/1000
  −0.01、pso/1006 −0.02、pso/1016 −0.01、genetic-target/1014 −0.01)。
  **v2 与 v1 的组排名完全相同** — carbon 上排列序效应可忽略。

**v2 主指标配对结果(paired vs random,20 seeds,结论与 gen-4 v1 一致):**

| group | v2 unique/100 | paired Δ | wins | coverage |
|---|---|---|---|---|
| pso-target | 114.35±27.41 | +44.35±27.51 | 18/20 | 43.21 |
| pso | 97.78±54.71 | +27.78±52.73 | 14/20 | 41.67 |
| genetic-target | 80.66±21.60 | +10.66±24.60 | 13/20 (n.s.) | 41.54 |
| target_region | 72.18±12.56 | +2.18±14.38 | 10/20 (n.s.) | 42.68 |
| random | 70.00±5.95 | — | — | 36.38 |
| genetic | 59.16±25.24 | −10.84±28.71 | 8/20 (n.s.) | 39.47 |
| random-reuse | 24.45±9.97 | −45.55±9.16 | 0/20 | 38.04 |

carbon 结论不受 gen-5 口径修正影响:fps 策略下 pso-target 仍是发现最优
(pso 方差极大、bimodal 延续),random-reuse 在 fps 下仍是毒药(与
2026-10-02 选择策略 sweep 一致)。

## PdCuNiP 结果(2026-10-06 完成 140/140,分析同日)

sweep `20261004T031310Z` 收尾:10-05 23:24 进程被外部终止(机器未重启、无
traceback),缺 seed 1019 共 6 个 run,由 `benchmark/resume_sweep.py` 从检查点
补齐(dry-run 物料守卫先过:冻结配置核对 + 行身份检查)。完整性:140/140、
无重复键、全部 `max_evaluations`、单数据集 `ds_9ca89d14f8f0`、SHA256SUMS
校验通过。**注意:resume 的 6 行在空载机器上跑,wall 与整夜争用下的行不可比。**

**bit-exact 审计(对照同代码 gen-4 存档 `20261002T005922Z`)——通过。**
140 对 (optimizer, seed) 逐行:行级轨迹字段(unique/raw/accepted/coverage/
evaluations/stopped_by/anchor_proximity/targeting_enabled)与逐轮共享字段全部
逐位一致、轮数一致;**resume 补跑的 6 行同样逐位一致**(确定性只依赖
seed+config,与机器负载无关)。gen-5 仅新增字段。

**口径分化:** v1 ≠ v2 仅 3/140 行(比 carbon 还少);pre-screen == archived
全部成立;**v2 与 v1 组排名完全相同**:

`random-reuse > genetic > random > pso > pso-target > genetic-target > target_region`

**v2 主指标配对结果(paired vs random,20 seeds):**

| group | v2 unique/100 | paired Δ | wins | coverage | prox median |
|---|---|---|---|---|---|
| random-reuse | 158.31±19.73 | +27.75±21.21 | 18/20 | 322.50 | 73.50 |
| genetic | 142.12±18.06 | +11.56±16.85 | 17/20 | 240.19 | 158.69 |
| random | 130.56±3.75 | — | — | 407.28 | 55.08 |
| pso | 127.51±32.48 | −3.05±31.33 | 7/20 (n.s.) | 348.36 | 140.56 |
| pso-target | 111.53±20.62 | −19.03±20.91 | 2/20 | 352.23 | 82.23 |
| genetic-target | 100.53±3.72 | −30.03±5.77 | 0/20 | 386.06 | 36.68 |
| target_region | 100.16±3.34 | −30.40±5.58 | 0/20 | 416.71 | 33.35 |

与 R4 时代 PdCuNiP 结论一致(2026-10-03 迁移记录):**random-reuse 在多组分
材料上是最优发现策略**(碳上它是毒药 — 排名不迁移);genetic 有真实的
coverage/致密化生态位(240.19 vs 407.28,20/20);定向机制迁移(TR prox
33.35 最优)但发现代价放大(−30.40);pso-target 在两材料上定向都失败。
无普适优化器默认值 — 新材料复用本冻结预注册重跑。

## 发布包与 L5 收口(2026-10-06)

- `benchmark/published/2026-10-06-f-gen5/`:carbon/ + pdcunip/ 两个自含子包
  (config.frozen.json + run_results.jsonl + summary.json + environment.json +
  SHA256SUMS,LF 清单,自包根 `sha256sum -c <mat>/SHA256SUMS` 校验通过)+
  顶层 README(指标契约、可复现范围、两材料结论、数据集获取方式)。
  `.gitattributes`(`benchmark/published/** -text`)保证入库字节一致。
- **L5(独立复现)数据侧就绪**:包内可完整重算全部配对分析表格;描述符
  重算需按 README 的获取方式取得两个数据集。

## 状态(F 完成)

- F1–F4 完成;carbon + PdCuNiP 双 sweep 完成、双审计通过、双口径分析完成;
  发布包已构建。**Milestone F 关闭,2026-10-02 改进计划(A–F)全部完成。**
- 运维事故记录:①2026-10-04 同秒双启动(035034Z 污染,隔离于
  `benchmark/_quarantine/`);②2026-10-05 夜间 PdCuNiP 进程外部终止
  (resume 恢复,6 行补齐)。sweep 活性核查一律用 run_results.jsonl 行数,
  不要信 exec 完成通知。
- 未提交:F 记录文档 + 发布包,等用户示意。
