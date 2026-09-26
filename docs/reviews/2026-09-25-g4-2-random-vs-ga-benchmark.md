# 2026-09-25 — G4-2 Random vs GA benchmark 完成记录

前置:G4-1(`2026-09-25-g4-1-genetic-optimizer.md`)、USPEX 分析
(`2026-09-25-uspex-ga-analysis.md`)。脚本 `benchmark/genetic_vs_random.py`,
结果 `benchmark/results/20260925T042355Z/genetic_vs_random.json`(60 runs 全部完成)。

## 配置

carbon 数据集(ds_d56748fb4391,6738 帧)+ NEP descriptor run
(run_57a8b8c40286,atom 级 35 维,robust scaling);objective =
local_environment_novelty(threshold 0.25, top_fraction 0.2);
operators = displacement(0.15 Å)+ isotropic/anisotropic strain(0.05)+
shear(0.05);n_seeds=64 × children_per_seed=8(512 提案/轮),batch_accept=8;
预算 10000 evals(≈20 轮),discovery stop 关闭;20 个 seed(1000–1019),
每个 seed 三配置成对:random / random-reuse(reuse_accepted_seeds=True,
Random 最强形态)/ genetic(G4-1 默认参数:parent_fraction 0.7,
immigrant_fraction 0.15)。单 run ≈ 4.5 min,总 ≈ 4.5 h。

顺带修复了使能该指标的 engine bug:unique_novel_environments 原在 accepted
行写入 local_archive **之后**计数,每个候选到自己的块距离为 0,指标结构性
恒 0;已改为对**轮前** frozen archive 计数(A12 文档语义),回归测试
`test_unique_count_runs_against_the_pre_round_archive`。该指标不参与验收,
Random golden 基线逐位不变;修复后全量 542 passed + 1 skipped。

## 结果(unique_novel_environments / 100 evals,20 seeds)

| 优化器 | 均值 | 中位数 | 标准差 | coverage radius 均值 | accepted 均值 |
| --- | --- | --- | --- | --- | --- |
| random | **25.31** | 25.09 | 1.50 | 36.19 | 177.9 |
| genetic(G4-1 默认) | 18.86 | 15.38 | 8.64 | 34.52 | 191.2 |
| random-reuse | 11.43 | 10.84 | 3.62 | **33.72** | 215.5 |

成对(genetic − random):**−6.45 ± 8.74,7/20 胜**;coverage radius
genetic 好 11/20(−1.67)。genetic − random-reuse:+7.44,16/20 胜。
random-reuse − random:−13.89,**0/20 胜**。

## 结论

1. **G4-1 v1 默认参数在 10k 预算下不是 discovery 的赢家**(7/20,均值 −6.45),
   尽管它决定性地优于 random-reuse(16/20)并在 coverage radius 上略优。
2. **random-reuse 是明确的反面教材**:复用已接受结构做父代在 novelty 指标上
   0/20 全败(−13.89 ± 3.46)——纯开发(exploitation)是 discovery 的毒药;
   建议文档中把该开关默认关(现状)并标注此数据。
3. **GA 的失败模式是早期谱系锁定,不是后期乏力**。逐轮轨迹:输的 seed
   (如 1006)round 1 与 random 相当(135 unique),round 2–4 坍缩到 8–23;
   赢的 seed(如 1010)全程 84–156。**末 5 轮产出 GA 全面高于 random**
   (41.9 vs 23.5、51.6 vs 24.5、40.3 vs 40.3、36.0 vs 35.1)——锁定到好
   谱系后 GA 的收割效率反而更高。亏损集中在 round 2–4:首轮仅 8 个 accepted
   入池,parent_fraction 0.7 + 平方轮盘把 512 提案/轮的 85% 压在 6 个池父代
   的窄谱系上,immigrant 15%(≈77 提案/轮)补不上 breadth。
4. GA 方差双峰(7 seed 达 26–33,超过 random 的 22–28 带;其余 9–13):
   机制有效但初始化敏感——好谱系 = 大胜,坏谱系 = 小负。

## G4-1.5 行动项(按证据强度排序)

1. **降低早期选择压力**:池 < N(如 32)时用均匀/线性父代权重,或
   parent_fraction=1.0 起步随池增长收紧;直接针对 round 2–4 的坍缩窗口。
2. **移民份额随轮退火**(早期 0.3–0.5 → 后期 0.1),保护开荒期。
3. **AutoFrac 配额自适应**(记账已在 `state_dict.operator_stats`):把
   每算子 accepted/proposed 成功率反馈进 mask 权重,替代纯基因漂变。
4. 复测:同一 harness、同 20 seed,目标 = genetic ≥ random 且方差收窄。

## 运行注意事项

- harness 复刻 worker 装配但传 `workers=cpu_count`(生产同款);单线程
  nearest-row 扫描会吃掉 90% 墙钟(100s/轮 → 12s/轮),测的是搜索不是算力。
- 脚本逐 run checkpoint;`--pilot` 为 400-eval 冒烟。全程无 DB 写入。
- benchmark 的 wall-time 列仅供参考,headline 全部是预算归一化指标。
