# 2026-09-25 — USPEX 9.4.4 GA 实现分析(G4-1 前置调研)

源码:`tmp/USPEX-9.4.4`(解压自 `tmp/USPEX-9.4.4.tar.gz`),MATLAB 实现。
体相变体主循环在 `src/FunctionFolder/USPEX/300/EA_300.m`,共享算子在
`src/FunctionFolder/USPEX/src/`,PSO 是平行分支 `src/FunctionFolder/PSO/`。
下文行号均指 9.4.4 原始文件。

## 1. 总体架构:每代一个"评估 → 排名 → 造子代"循环

`EA_300.m` 的单代流程(编号对应文件行号):

1. **等待本代全部局域优化完成**(L10–27;USPEX 是外层循环提交 VASP 等计算,
   每个个体可能经过多步 relaxation `Step`,对应我们的多轮 descriptor evaluation)。
2. **适应度 → 相关性 → 反种子惩罚 → 排名修正**(L57–64):
   `CalcFitness_300` → `Correlation` → `AntiSeedsCorrection` → `FitnessRankingCorrection`。
3. **停止判定** `StopRun`(L74)。
4. **造子代**:`OFF_STRUC = POP_STRUC`,先放一个 elitist 起点(`QuickStart`),
   然后按固定顺序调用算子填满种群(L106–122):
   ```
   Operation = {Heredity, Random, Permutation, LatticeMutation, SoftModeMutation}
   Num_Opera = {howManyOffsprings, howManyRand, howManyPermutations,
                howManyMutations, howManyAtomMutations}
   ```
   每个算子 slots 依次填;**算子失败 50 次自动降级为 Random_300**
   (如 `Heredity_300.m` L16–19、`Mutation_300.m` L18–21)。
5. **精英保留** `KeepBestStructures` 把上一代最好结构并进新种群(L137)。
6. **更新算子配额与选择表** `update_STUFF`(L86),进入下一代。

对 G4-1 的映射:USPEX 的"代"≈ 我们的一个 round;USPEX 的 `populationSize`
≈ 我们的 `n_seeds × children_per_seed`;USPEX 让算子直接填满整代,而我们的
引擎已经拥有 selection(novelty + FPS),GA optimizer 只需在 `propose()` 内部
完成"选父代 → 施加算子",把选择权继续留给 engine——这与 G3.5 contract 的
分工一致,不需要改引擎。

## 2. 适应度与选择(最值得抄的部分)

### 2.1 排名选择表(tournament 数组)— `update_STUFF.m` L66–79

USPEX **不用连续实数 fitness 做选择**,而是只用**排名**。选择表是长度为
`howManyProliferate = bestFrac × 种群` 的整数数组(默认 `bestFrac=0.7`):

```
tournament(N) = 1;                    % 最差的可育个体 1 张票
tournament(i) = tournament(i+1) + i²  % 从后往前累加平方
```

即排名 r(1=最好)的**票数是平方的累加和** Σ_{k=1}^{N−r+1} k² ≈ (N−r+1)³/3,
选父代 = `find(tournament > randint(0, max−1))` 取最后一个命中
(`Heredity_300.m` L23–25),被选概率随离开最优名次的差距**三次方**衰减——
比线性轮盘陡得多,但比截断选择温和;**且对 fitness 的绝对量纲和单调变换
完全不敏感**——这正好绕开了我们 gen-4 fitness(novelty、coverage 等异质
量纲混合)不可比的问题。

**G4-1 建议**:父代选择直接按 `selection_rank`/accepted 标记构建平方票数表,
 fitness 数值只用于排序,不参与采样。

### 2.2 排名修正 `degradeSimilar` — `FitnessRankingCorrection.m` L8

排序后做**指纹去重**:与任何已排名结构 fingerprint 余弦距离 < `toleranceFing`
(默认 0.008)或 enthalpy≥9999 的个体**移到排名末尾**(`bad_rank` 计数),
不参与父代选择但保留在种群中。这就是 USPEX 版的"重复结构不淘汰但不生育"。

我们的 engine 已经在 selection 阶段做了 greedy union dedup(A12),GA 侧
对应的做法是:父代池只用 accepted 且非重复的候选,天然等价。

### 2.3 Anti-seeds:指纹空间的 Gaussian 排斥 — `AntiSeedsCorrection.m`

用户可提供一组"反种子"(已知不想要的结构),并把当前代的结构也逐步
加入(`antiSeedsActivation` 代之后,>0 全部加入,<0 只加每代最优)。惩罚:

```
fitness += Max · exp(−d²(fing, antisSeed) / (2σ²))
```

参数是**自适应的**:
- `σ = antiSeedsSigma × (top-bestFrac 内两两指纹距离均值)`(随种群分散度缩放);
- `Max = antiSeedsMax × (top 均值 fitness − 最低 fitness)`(随适应度 spread 缩放),
  且有下限 `≥ 0.5 × 上一代 Max`(防止惩罚塌缩到 0,L12–14)。

默认 `antiSeedsMax = 0`(关闭)。对 G4-1 这是可选机制:我们已有 novelty
objective + coverage,先把 anti-seeds 记为"负向多样性先验"的备选,不建议
第一版就加。

### 2.4 `Correlation`:fitness-成分相关系数 — `Correlation.m`

计算 fitness 与平均成分 order 的 Pearson 相关,存为 `cor_dir`,仅用于
heredity 切片时决定保留哪一侧的 order(`heredity_final.m` L42 起)。
变异-only 的 G4-1 用不到,仅记录语义。

## 3. 变异算子(G4-1 的直接原型)

### 3.1 原子位移变异 — `move_all_atom_Mutation.m`(src/)

核心正是计划里写的"位移 σ 连续基因":

1. **每个原子独立高斯位移**,σ 都等于 `max_sigma`(用户配 `howManyMut`;
   未配置时默认 **3× 平均共价半径**,物理量纲随体系缩放,
   `createORG_EA.m` L109–115);
2. **order 引导的非均匀缩放**:`koef = (r_N − r_I)/(r_N − r_1)`,即按原子
   order 参数(指纹侧的"好坏"度量)线性缩放——order 最差的原子位移全幅,
   最好的原子不动(L14–20)。我们目前没有 per-atom order,第一版可令
   koef ≡ 1,这是 USPEX 的加强项而非必需项;
3. **质心漂移抑制**(L21–31):若 `‖ΣΔr‖ > 1.5·max_sigma` 就重抽,然后把
   `Δr_i ← Δr_i − ΣΔr·(‖Δr_i‖/Σ‖Δr_j‖)`——整体平移被按比例扣除,方向不变。
   我们 A11 之后的 displacement 只有 per-atom hard cutoff,没有去质心漂移
   这一步;GA 版值得加上(平移对周期体系指纹无贡献,纯浪费评估)。

调用侧 `Mutation_300.m`:选父 → 变异 → `distanceCheck` + `CompositionCheck`,
不过就重抽(最多 50 次,再不行降级 Random),**重抽时 σ 按
`max_sigma × (1 − safeguard/100)` 逐步收缩**(L24),第 100 次失败后
safeguard 兜底直接返回未变异的父代(L39–43)——"失败就收敛幅度"这一手
是我们现有 displacement 没有的。

### 3.2 晶格应变变异 — `lattice_Mutation.m`(src/)

应变基因 = **6 个独立高斯分量** ε ~ N(0, mutationRate²)(默认 σ=0.5):

```
strain = [[1+ε1, ε4/2, ε5/2], [ε4/2, 1+ε2, ε6/2], [ε5/2, ε6/2, 1+ε3]]
new_Lattice = old_Lattice · strain
```

两个关键后处理:
1. **体积守恒**:按 `ratio = det(old)/det(new)` 的立方根回缩,把应变约束成
   等容变形(L21–25)。常数体积 + 形状探索,避免"胀大盒子"这种低价值方向;
2. **晶格约化** `latConverter`(Niggli 类),应变换后重新取约化胞。

调用侧 `LatticeMutation_300.m` 用的是 `lattice_atom_Mutation.m`(300/):
**应变 + 全原子位移一次施加**,原子位移部分与 3.1 相同(σ 用 `howManyMut`)。

### 3.3 置换变异 — `swapIons_mutation_final.m`(src/)

交换 1~`howManySwaps` 对不同种类原子;配对选取是 50% 取 order 最差的两个
原子、50% 按 order 的平方轮盘(`Roulette`,L28–33)——同样是"order 引导"。

### 3.4 软模变异 — `SoftModeMutation_300.m`

预计算声子软模(`calcSoftModes`),沿最低频特征向量 ±方向移动原子。
这是 USPEX 的招牌算子之一,但依赖力场/二阶力常数,与我们 descriptor-only
的定位不匹配,**G4-1 明确不做**。

## 4. 交叉算子(Heredity,供 G4-1 之后的完整性参考)

`Heredity_300.m` + `heredity_final.m`:
- 两个父代沿**随机维度**切片拼接(`fracFrac ∈ [0.25, 0.75]` 均匀抽样),
  切前两父代各自随机平移(`percSliceShift` 控制单维平移 vs 三维平移);
- `manyParents>1` 时支持多父代版 `heredity_finalMP`;
- lattice 来自父代之一按 slab 数加权缩放,再 `optLattice` 约化;
- 失败 50 次换父代重来,20 次产不出合法子代就整体放弃、重新选父。

USPEX 论文里 heredity 是主力算子(fracGene 默认 0.5),但它面向**找基态**,
父代即"好结构"。我们的目标是**覆盖新环境**(novelty/coverage fitness),
父代是"已接受且多样的结构",滑移拼接对 descriptor 环境覆盖的意义弱得多。
**G4-1 维持 mutation-only 的决策有依据,不需要先做 crossover。**

## 5. 随机结构与初始种群

- 初始种群 = 纯随机结构(`Random_Init_300`,随机对称群 + 随机胞参数),
  组成按 `firstGeneSplit` 随机分块;
- 每代注入 `howManyRand`(默认 0.2)个全新随机结构——**持续 immigrants**,
  USPEX 防早熟的第一道保险。
- 我们没有随机造胞能力(种子全部来自数据集),等价物是每轮保留一定比例
  的"未变异 seed pool 抽样"提案——Random optimizer 已经在做,GA 版可以
  把" immigrant 比例"作为参数保留(比如 10–20%)。

## 6. 精英保留 — `KeepBestStructures.m`

新种群 = 算子子代 + 上一代前 `keepBestHM` 个结构。保留时同样过**指纹去重**
(与已保留的和彼此之间比较,`toleranceBestHM`),且 `dynamicalBestHM` 模式
会按"top 均值 + 一个标准差"动态截断保留数量。3D 变组分体系还会把 convex
hull 上的结构全部强制保留(`convex_hull` 列)。

对应到我们:engine 的 archive 已经是持久精英库;GA optimizer 的"精英"只需
保证**每轮一定比例的提案直接以 accepted/高分个体为父**(相当于 low-σ 收缩
变异的精修 exploitation),不需要自己再养一个 elite 池。

## 7. 算子配额自适应(AutoFrac)— `update_STUFF.m` L22–63

开启 `AutoFrac` 后,每代统计:上一代**新产生**的 top-bestFrac 结构各由哪个
算子产生(`N(1,:)`)、全部种群各算子占比(`N(2,:)`),得成功率
`X(i) = N(1,i)/N(2,i)`,归一后与用户先验混合:

```
f = 0.55·用户分数 + 0.45·成功率分数    (并保证 rand/gene/atomsMut 各 ≥ 0.10)
```

这正是计划里 **operator mask** 的学习版:mask 不是 0/1 开关,而是平滑的
success-rate 配额。G4-1 可以先实现固定配额(对应 USPEX 非 AutoFrac 的
fracRand/fracGene/… 用户输入),把 AutoFrac 式的成功率自适应记为 G4-1.5
增强项——实现上只需要 `observe()` 里按 `selection_rank < K` 给算子记账,
一个 dict 就够。

## 8. 停止条件 — `StopRun.m`

三条,满足其一即停:
1. 达到 `numGenerations`(默认 100);
2. 达到已知目标 fitness `stopFitness`(如已知基态能量);
3. `SameBest`:连续 `stopCrit` 代最优结构指纹不变(`toleranceF=0.01`,
   `SameBest.m` 需要连续 stopCrit−1 次相同)。

我们的 discovery-rate stop(A04)在语义上对应第 3 条(新环境产出枯竭)。
GA 不需要新增停止条件,沿用 engine 的即可。

## 9. 防早熟机制汇总(USPEX 的"配方")

1. 持续随机 immigrants(≥0.2);
2. 指纹去重:排名降级(degradeSimilar)+ keepBest 去重 + heredity 父代
   距离下限 `maxDistHeredity`;
3. anti-seeds Gaussian 排斥(自适应 σ/Max,默认关);
4. 平方轮盘选择 + bestFrac 截断(压力可调,极端时用 `softMutOnly` 收缩);
5. AutoFrac 算子配额自适应;
6. 算子失败降级 Random(永不空转)。

我们已有 2(engine dedup)、部分 5(A12 起步);G4-1 要补的是 1(immigrant
比例)、4(平方轮盘)、6(降级路径),3 保持可选。

## 10. 对 G4-1 的落点建议

按 `docs/reviews/2026-09-25-g35-correctness-and-optimizer-lifecycle.md` 的
gen-4 contract(`OptimizationContext / ProposalBatch / ObservationBatch`),
USPEX 机制与现有代码的对应:

| USPEX 概念 | G4-1 落点 | 备注 |
| --- | --- | --- |
| tournament 平方轮盘(排名制) | GA optimizer 内部父代采样 | 只用 accepted + selection_rank,不吃 fitness 量纲 |
| `move_all_atom_Mutation`(σ 基因) | `operators/displacement.py` 已有 max_sigma;GA 把 σ 提升为**个体基因组** | 加去质心漂移项 |
| `lattice_Mutation` 6 分量应变基因 | `operators/strain.py` 现为各向同性;GA 需新的各向异性 6 分量 + 体积守恒 | shear.py 可复用部分逻辑 |
| operator mask / AutoFrac | 基因组中的算子掩码 + observe() 记账 | 第一版固定配额,成功率自适应留 G4-1.5 |
| Random immigrants | propose() 中固定比例直接从 seed_pool 出未变异提案 | 参数名建议 `immigrant_fraction`,默认 0.1–0.2 |
| 算子失败降级 | propose() 内 try/约束预检,失败换算子/父代,最终兜底 displacement | 引擎的 geometry gate 兜底不变 |
| degradeSimilar / keepBest 去重 | engine 已有,optimizer 只用 accepted∩非重复 | 不新增逻辑 |
| SoftModeMutation | **不做** | 依赖二阶力常数 |
| Heredity | **不做**(G4-1 mutation-only) | 目标是环境覆盖而非基态搜索 |
| AntiSeeds | 可选,第一版不做 | novelty objective 已覆盖同语义 |

### 建议的基因组(G4-1 草案)

个体 = (σ_disp, strain_6, mask_weight_per_operator),每轮 propose:

1. 从"accepted 且非重复"池按平方轮盘选父(immigrant 槽位除外);
2. 对每个子代:按 mask 抽一个算子 → 基因给出该算子的幅度参数
   (σ_disp 或 ε_6)→ 施加 → 轻量预检(最小距离),失败重抽 ≤3 次;
3. observe():按 selection_rank 给所用算子记成功账(为 AutoFrac 留钩子);
4. state_dict():基因组矩阵 + 算子记账,可序列化。

预算参照:`GENERATION_ALGORITHM_VERSION` **不需要** bump——版本号守的是
持久化指标语义的可比性,新增 optimizer 是纯增量(random 的指标、golden
基线逐位不变),per-run 的 `optimizer` 字段已区分算法来源,G4-2 的
Random vs GA benchmark 用 `unique_novel_environments / 100 eval` 与
accepted-only coverage radius 评,GA 的收益预期来自"父代质量高 + 幅度
基因自适应",benchmark 能直接回答 mask/自适应是否值得。
