# 2026-09-25 — G4-1 GA mutation-only 完成记录

前置调研:`docs/reviews/2026-09-25-uspex-ga-analysis.md`(USPEX 9.4.4 源码分析)。
本变更新增 **genetic optimizer**(mutation-only GA),不动 Random 的任何行为,
`GENERATION_ALGORITHM_VERSION` 保持 **gen-4**(纯增量,持久化指标语义未变,
per-run `optimizer` 字段已区分算法来源)。

## 落点

| 内容 | 文件 |
| --- | --- |
| `GeneticOptimizer`(genome + 平方轮盘 + mask + immigrants + 降级) | `generation/optimizers/genetic.py` |
| 注册 + catalog 默认参数 | `generation/optimizers/__init__.py`、`generation/registry.py` |
| submit 期完整校验(未知键、count、fraction 边界) | `generation/models.py` |
| 前端:类型/默认值/提交映射/配置 UI/标签/中文文案/preview catalog | `features/generation/{types,generationStore,submission,labels}`、`pages/generationConfig.tsx`、`i18n/zh.ts`、`preview.tsx` |
| 测试(31 个:生命周期/形状/基因/掩码/观察/选择/降级/引擎集成/请求解析) | `tests/test_generation_genetic.py` |

## 机制(USPEX → 本实现)

* **平方累加排名轮盘**(USPEX `update_STUFF` tournament):父代只从 accepted
  池选,排名 r 的票数 Σk²,抽中概率随名次差距三次方衰减;只吃
  `selection_rank` 不吃 fitness 数值,对 gen-4 异质指标量纲免疫。
* **连续幅度基因**:池中每个父代带 genome = (σ_disp, max_strain, max_shear,
  mask 权重)。子代 = 父代 genome 变异(相对高斯,clip 到算子层校验边界),
  用变异后基因施加算子;被接受的子代带着**真实产生它的基因**入池——
  选择驱动的 USPEX σ 收缩等价物。基因是幅度上限,与算子既有
  distribution-bound 语义(A11)一致,算子层零改动。
* **算子掩码**:每个启用算子一个权重,轮盘抽算子;mask 随基因遗传变异,
  下限 0.05(USPEX 式算子地板,永不彻底关死,可再被选中)。
* **移民**(USPEX `howManyRand`):每轮固定 `immigrant_fraction`(默认 0.15)
  的父代槽位直接来自 seed pool(全新基因组),防早熟;池为空的首轮全部
  是移民,分布与 Random 首轮一致(G4-2 对比友好)。
* **降级路径**(USPEX retry → Random):mask 选择的算子连续 3 次失败 →
  退化为 genome σ 的纯位移变异;`parent_fraction`(默认 0.7,USPEX
  bestFrac)之外的池排名不参与抽选。
* **成功记账**:`observe()` 累计每算子 proposed/accepted(AutoFrac 钩子,
  G4-1 只在 state_dict 上报,成功率自适应留 G4-1.5)。

## 明确不做(依据调研结论)

* Heredity/crossover(面向找基态,与本系统"环境覆盖"目标不匹配);
* 软模变异(依赖二阶力常数);
* Anti-seeds(novelty objective 已覆盖同语义);
* 引擎侧任何改动(选择/验收仍完全由 engine 持有,G3.5 分工不变)。

## 验证

* 后端:`pytest tests` — **541 passed, 1 skipped**(含 Random golden 基线
  `test_generation_random_baseline.py` 逐位不变、GA 新增 31 个测试)。
* 前端:`tsc -b`、`eslint`、`vitest run` **225 passed**。

## 尚未开始

* G4-2 Random vs GA benchmark(同 seed、同预算 10000 descriptor evaluations、
  20 repeats,核心指标 `unique_novel_environments / 100 eval` 与
  accepted-only coverage radius);
* G4-1.5 候选:AutoFrac 式成功率自适应配额(记账已就位);
* G5 PSO / target-region / External adapter。
