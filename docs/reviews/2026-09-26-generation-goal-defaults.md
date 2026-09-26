# 2026-09-26 — 生成目标默认方案(基于三轮 sweep 实测)

依据:G4-2(`2026-09-25-g4-2-random-vs-ga-benchmark.md`)、
G4-1.5(`2026-09-25-g4-1-5-warmup-autofrac.md`)、
G5-1(`2026-09-25-g5-1-pso-memory.md`)、
G5-2(`2026-09-25-g5-2-target-region.md`)四轮 20-seed 对照 sweep。
本记录把实测结论落成**按生成目标选择的默认方案**,并同步到代码默认值
与 UI 提示。

## 决策表

| 生成目标 | 优化器 | 关键参数 | objective | 实测依据 |
| --- | --- | --- | --- | --- |
| **全局发现新环境**(默认) | `random`(`reuse_accepted_seeds=false`) | n_seeds 64 × children 8,batch_accept 8 | local_environment_novelty(0.25 / top-20% / robust) | unique/100 **25.31±1.50** 全场最优且方差最小;所有学习型优化器(GA −6.45、PSO −7.09、TR −1.5)都未能超过它 |
| **定向补采样**(围绕指定结构) | `random` + **搜索目标**(顶层 `anchor_frames` 1–16 个锚点帧,`region_radius=15`) | 锚点须满足几何约束;与 `reuse_accepted_seeds` 互斥 | 同上 | accepted→锚点中位距离 **20/20 全部更近**(25.50 vs 47.70),radius 内 21.2% vs 0%;discovery 代价仅 −6%。2026-09-26 重构后定向是搜索轴而非方法(`2026-09-26-search-target-refactor.md`) |
| **已接受区域加密** | `random`(`reuse_accepted_seeds=true`) | 同 random;约半数父结构来自多样化 accepted 池 | 同上 | coverage radius 全场最优(33.72,14/20 优于 random)——更紧的 accepted 分布正是"加密"的语义;其 discovery 损失(0/20)在该目标下不是缺点 |

**明确不推荐为主力的**:genetic(G4-2 负结果,但优于 random-reuse 16/20,
保留为进化策略基线/实验选项)、pso(G5-1:方差 ±11.89 全场最高、
coverage 劣化 +3.87,UI 保留)、G4-1.5 暖启动/AutoFrac(净负,已回退,
机制参数化保留)、crossover/NSGA-II(计划条件项:GA 无优势,不触发)。

## 已同步的代码默认值

1. `region_radius` 默认 **10 → 15**,并升格为请求顶层字段(与
   `anchor_frames` 一起构成搜索目标轴,不再属于任何优化器的参数)——
   单次变异的描述符漂移实测 13–18 个稳健单位,10 会让子代落回权重
   窗口之外(20/20 的胜利用的就是 15)。
2. 三个学习型优化器的 UI 描述卡各加一行**实测结论提示**(中英文):
   遗传算法、PSO 的负结果与适用定位,target_region 的 20/20 正结果。
3. 其余默认值经实测确认不动:random 的 n_seeds=64 × children=8、
   batch_accept=8、reuse 默认关;GA 的 parent_fraction=0.7 /
   immigrant=0.15(v1 实测形态);objective 默认
   local_environment_novelty;发现率停止(window 10,≥1 新环境/100 evals)
   保持开启——对定向补采样它语义变为"区域已饱和",同样是正确的停止理由。

## 使用注意

* 三轮 sweep 全部基于 carbon 数据集 + NEP 描述符(atom 级 35 维)。
  `region_radius=15` 的标定随描述符敏感性变化,换描述符后若发现
  accepted 距离中位数系统性高于半径,应按"半径 ≥ 单次变异漂移"重标。
* 随机种子固定时四条管线完全可复现(random 在三轮 sweep 中逐位一致,
  三次交叉验证)。
* 提交提示:`.gitignore` 忽略 `docs/`,新增记录需 `git add -f docs/reviews/`。
