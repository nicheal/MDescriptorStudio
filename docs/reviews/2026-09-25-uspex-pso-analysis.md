# 2026-09-25 — USPEX 9.4.4 PSO 实现分析(G5 前置调研)

源码:`tmp/USPEX-9.4.4/src/FunctionFolder/PSO/`(MATLAB)。与 GA 分支
(`USPEX/300/EA_300.m`)平行,主循环结构几乎相同(评估 → 排名 → 造子代 →
精英保留),差异全部在**父代选择/记忆**与**子代产生方式**。
前置文档:`2026-09-25-uspex-ga-analysis.md`。

## 1. 核心机制:粒子记忆 + 距离调制的重组概率

### 1.1 粒子与记忆(PSO.m L56–70)

每个种群槽位 `ind` 是一个粒子;`POP_STRUC.PSO(ind)` 存该粒子的
**个人最优 pbest**(lattice/coordinates/order/fitness/fingerprint 逐项拷贝),
`bestPSOstruc` 是全体 pbest 中的**全局最优 gbest 索引**。每代评估后:

```
若 generation == 1 或 pbest.fitness > 当前 fitness → pbest ← 当前结构
gbest ← argmin_i pbest[i].fitness
```

没有速度向量、没有惯性项——USPEX 的"速度"被重释为**算子选择概率**。

### 1.2 吸引力 = 指纹距离调制的重组(PSO.m L100–115 + Heredity_PSO.m)

每代对每个粒子:

```
dist1 = cosineDistance(current.fingerprint, pbest.fingerprint)   # 离自己历史最优多远
dist2 = cosineDistance(current.fingerprint, gbest.fingerprint)   # 离全局最优多远
P_p = rand · PSO_BestStruc · dist1        # 拉向 pbest 的概率质量
P_g = rand · PSO_BestEver  · dist2        # 拉向 gbest 的概率质量
P_m = rand · PSO_softMut                  # 软模变异的概率质量
P_r = fracRand                            # 随机移民

if rand < P_r 或 该粒子连续失败(Error > maxErrors) → Random_PSO(整粒子重置)
elif rand < P_m / (P_p + P_g + P_m)     → SoftModeMutation_PSO
else                                     → Heredity_PSO(吸引子移动)
```

`Heredity_PSO` 内部(L18–19)再按
`tmp = rand·(PSO_BestStruc·dist1 + PSO_BestEver·dist2)` 与
`PSO_BestEver·dist2` 比较决定吸引目标:

- **Heredity-g**:`crossover(gbest, current)`;
- **Heredity-l**:`crossover(pbest[ind], current)`。

crossover 本体与 GA 的 `heredity_final` 完全一致(切片拼接 + order 引导
slab 搜索),唯一区别是父代 1 来自 PSO 记忆(`heredity_final.m` L7–17:
`par_one == 0` → gbest,否则 → pbest)、父代 2 是粒子当前位置。

**关键解读**:吸引强度 ∝ 指纹距离——粒子离最优越远,拉力越大,与经典
PSO 的 velocity×(pbest−x) 同构,只是"位移"发生在结构空间、由 crossover
实现,而距离用 descriptor 指纹度量。**这正是"descriptor 作为优化器的
导航坐标"的最直接用例。**

### 1.3 其余与 GA 共享

排名修正(degradeSimilar)、AntiSeeds、KeepBest(fingerprint 去重精英)、
StopRun 三条件、失败 50 次降级 Random——全部同一套(`KeepBestStructures_PSO`
比 GA 版少了 convex-hull 分支)。

## 2. 对我们 G5 的映射与难点

| USPEX 概念 | 我们的对应 | 备注 |
| --- | --- | --- |
| 粒子 = 当前结构 | 引擎无粒子概念;按 slot 追踪 lineage | optimizer 需自建 slot→candidate 映射 |
| pbest/gbest 记忆 | `observe()` 更新,存 (candidate, fitness, descriptor) | fitness 已有;descriptor = `structure_descriptor` |
| 指纹距离 dist1/dist2 | 缩放后 descriptor 的欧氏/余弦距离 | 与 engine 的 FPS/selection 同一空间 |
| 拉向 pbest/gbest = crossover | **没有 crossover 算子** | 见下,这是 G5 的核心难点 |
| 软模变异 | 不做(无力常数) | 概率质量归还给变异/移民 |
| 随机移民(含失败重置) | seed pool 槽位,同 GA | 已有实现模式 |

**难点:没有 crossover,PSO 的"吸引"无法在几何上实现。** 三个选项:

1. **实现 crossover**(切片拼接或描述子引导的原子交换)。GA 阶段否决
   crossover 的理由是"环境覆盖目标下父代=已接受结构,拼接收益弱";但
   PSO 的父代是 pbest/gbest(历史最优),语义不同——这是把它重新搬上台面
   的理由。代价:算子层新增结构重组算子 + 组分/原子数锁约束下的实现复杂度。
2. **记忆化 GA**(particle = GA 个体 + pbest 记忆):粒子槽位按距离调制
   的概率选择"从自己的 pbest 变异"还是"从全局池变异";没有真吸引,
   但保留了 PSO 的记忆结构与开发/探索平衡。实现成本低,可作为 G5 第一版。
3. **不做 PSO**,G5 只做 External adapter。若 G4-2 显示 GA 已接近饱和收益,
   PSO 的边际价值存疑。

**建议**:G5 先做选项 2(PSO-as-memory-GA,~200 行,复用 GA 全部机制),
把选项 1(crossover 算子)记为独立决策项;选项 3 由 G4-2 数据决定是否触发。

## 3. 参数量级(来自代码语义)

- `PSO_BestStruc`/`PSO_BestEver`:两个吸引系数,与 fracRand、softMut 同
  量纲(概率权重);代码中无默认值(用户输入),直觉起点:gbest 系数
  ≥ pbest 系数 > 0,immigrant ≥ 0.1(与 GA 的 parent/immigrant 框架对齐)。
- 失败粒子直接整粒重置为随机(`Error > maxErrors`),比 GA 的"单子代降级"
  更激进——粒子级健康检查。

## 4. 结论

USPEX-PSO 的本质是:**带个体记忆的 EA,其中记忆以"重组目标"的形式
作用于探索方向,距离(在 descriptor 空间)决定记忆的权重**。它与我们的
optimizer contract 完全兼容(记忆状态在 optimizer 内,observe 驱动更新),
唯一缺口是 crossover 算子。G5 落地顺序建议:memory-GA 版 PSO → 视效果
决定是否补 crossover → External adapter(独立于 PSO,随时可做)。
