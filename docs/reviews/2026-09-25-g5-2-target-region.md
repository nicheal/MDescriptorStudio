# 2026-09-25 — G5-2 Target Region(定向补采样)完成记录

前置:`06-plan-descriptor-guided-dataset-expansion.md` §7 G5、
`2026-09-25-uspex-pso-analysis.md`。本项按 2026-09-25 用户现场指令实现:
**让用户在 descriptor 空间指定目标区域(如"围绕某类高压相环境"),
探索机制朝该区域集中提案——从"全局找未知"变成"定向补采样"。**

## 实现(`generation/optimizers/target_region.py`,name = `target_region`)

* **区域定义**:锚点 = 用户指定的数据集帧(`anchor_frames`,1–16 个整数
  索引);锚点帧的缩放结构描述符即区域中心。
* **提案集中**:父结构按 `exp(-(d/r)²)` 权重采样(d = 到最近锚点的 L2
  距离,r = `region_radius`,稳健缩放单位);固定移民份额(默认 0.15)
  保持全局广度。验收仍由 engine 的 novelty/FPS 把关——优化器只改变
  提案分布,契约不破。
* **免费描述符**:任何数据集帧的描述符都是 frozen reference 的一行,
  锚点与种子池的描述符零描述符调用即可得;context 契约新增
  `seed_descriptors` / `anchor_descriptors` 两个字段(engine 仅转发,
  核心循环零改动)。
* **强制入池**:锚点帧强制进入种子池(worker)——否则锚点大概率不在
  512/6738 的抽样里,区域附近没有任何可变异的父结构。
* **病态锚点快速失败**:worker 校验每个锚点帧自身满足 run 的几何约束,
  违规直接拒绝提交(实测:frame 3427 是 2 原子亚阈值结构,不拦则 97%
  定向提案被 minimum_distance 拒绝,整轮预算被浪费)。
* **半径标定**(实测,NEP + robust scaling,carbon 数据集):单次位移
  变异(σ≤0.15 Å)移动结构描述符 ~13–18 个稳健单位,无关帧相距
  ~40–60;默认 `region_radius=10`,sweep 用 15。半径远小于单次变异
  漂移 = "只反复变异锚点本身"(子代永远回不到权重窗口)。
* 一次设计试错:**前沿地板**(K 近邻权重下限)曾实现后回退——在
  513 条池上它把 ~43% 的质量分给了 27+ 距离的远端种子,稀释区域;
  纯高斯 + 标定半径更干净。

**与计划原文的偏差(落档)**:计划 §7 G5 写的是 PCA 空间目标项
`F = −‖P(z(X)) − t‖`(PCA 框选);按用户指令改为描述符空间锚点 +
提案侧集中。两者可共存——P 是线性映射,未来可在 objective 侧叠加
PCA 目标项;UMAP/t-SNE 仅可视化的原则不变。

## 测度(20 seeds × 10000 evals,random 同 sweep 配对,`benchmark/results/20260925T151421Z/`)

| 指标 | random | target_region(r=15) |
| --- | --- | --- |
| **accepted→锚点距离中位数** | 47.70 ± 0.44 | **25.50 ± 1.59** |
| 距离中位数更近的 seed 数 | — | **20/20** |
| **radius 内 accepted 占比**(r=15) | **0.0%** | **21.2%** |
| unique novel env / 100 evals | 25.31 ± 1.50 | 23.82 ± 1.20 |
| 全局 coverage radius 均值 | 36.19 | 43.08 |
| accepted 数均值 | 177.9 | 211.1 |

## 结果

1. **定向补采样成立且决定性**:accepted 结构到锚点的中位距离近乎减半
   (47.7 → 25.5),**20/20 个 seed 全部更近**;21.2% 的 accepted 落入
   半径 15 的区域内(random 为 0%)。
2. **代价很小且明确**:全局 discovery(unique/100)仅降 6%
   (25.31 → 23.82),全局 coverage radius 变差(36.2 → 43.1,预期内——
   提案集中必然牺牲广度);accepted 数更多(211 vs 178,区域内结构
   novelty 衰减慢)。
3. 这是本系列第一个在自身目标指标上**全 seed 一致胜出**的优化器:
   "指哪打哪"的语义按设计兑现。

**剩余路线**(超出本阶段,已识别未实现):energy/force/uncertainty
筛选(MLP 两层,需训练基础设施)、PCA 框选锚点的 UI 交互、
PCA 目标项 objective(与现有提案集中可叠加)。

## 全局计划状态索引(生成新结构子系统)

权威计划:`docs/plan/06-plan-descriptor-guided-dataset-expansion.md`
(G0–G2 本轮范围,G3–G5 为后续路线)+ G3.5 外部评审
(`2026-09-25-g35-correctness-and-optimizer-lifecycle.md`)。

| 计划项 | 状态 | 记录 |
| --- | --- | --- |
| G0 重构基础 / G1 Random Expansion / G2 局域环境 | ✅ | `06-plan-...` 与历次 pass 记录 |
| G3 Iterative Maximin(FPS 接入、coverage objective、发现率停止) | ✅ | G3.5 记录 |
| G3.5 正确性门 + optimizer lifecycle(A01–A12/§25) | ✅ 已推送 0d414b8 | `2026-09-25-g35-...md` |
| G4-1 GA mutation-only(genome = σ/strain/shear + mask) | ✅ | `2026-09-25-g4-1-genetic-optimizer.md` |
| G4-2 Random vs GA benchmark(10000 evals × 20) | ✅ | `2026-09-25-g4-2-...md`(GA 负结果) |
| G4 crossover + NSGA-II(**条件项**:仅当 GA 有优势) | ✅ 按条件不做 | G4-2 结论:GA 无优势 |
| G4-1.5 暖启动/AutoFrac(评审后追加) | ✅ 实验+回退 | `2026-09-25-g4-1-5-...md` |
| G5-1 PSO(memory,无 crossover) | ✅ | `2026-09-25-g5-1-pso-memory.md`(负结果,方差最高) |
| G5-2 Target Region | ✅ 本记录 | — |
| G5 External adapter | ❌ 用户取消(2026-09-25) | 本节落档 |
| 能力扩展:energy/force/uncertainty 筛选(MLP 两层) | ⬜ 剩余路线 | 需 MLP 训练基础设施(仓库中不存在),超出本阶段 |
| UI 增强:PCA 框选锚点(替代手输帧号) | ⬜ 剩余路线 | 依赖 Analysis PCA 交互选择器 |

## 验证

后端 `pytest tests` 582 passed + 1 skipped(含 target_region 15 个测试;
Random golden 基线逐位不变);前端 tsc/eslint/vitest 全绿。
