# 零行为架构拆分:generation/engine.py → counting / records / selection(2026-10-06)

F 收口后队列(评审 §18)的第一项。`generation/engine.py` 当时 1918 行,其中
约 410 行是与迭代循环无关的纯函数/dataclass。本次把它们拆到三个内聚模块,
**零行为**:黄金基线逐位一致、全量后端 822 passed + 1 skipped + 0 failed
(与拆分前完全相同)、RNG 消费序列不动。

## 拆分内容

| 新模块 | 迁入成员 | 行数 |
|---|---|---|
| `generation/counting.py` | `_candidate_novel_rows`、`count_strict_unique_environments`(gen-4 访问序)、`_canonical_row_order`、`count_strict_unique_environments_v2`(gen-5 canonical) | 178 |
| `generation/selection.py` | `_fitness_elite_order`、`select_diverse_batch`、`SELECTION_STRATEGIES`、`select_local_incremental_batch` | 167 |
| `generation/records.py` | `RoundRecord`(+`to_json`)、`EvaluatedRecord`、`GenerationRunResult` | 126 |
| `generation/engine.py`(保留) | `GenerationEngine`(run 循环、几何/去重门、snapshot 三件套)、SNAPSHOT_VERSION、快照 schema 常量、RNG 编解码、candidate 编解码、四个 fingerprint 助手、`_geometry_rejection_code`、`_finite_or_none` | 1918 → 1523 |

依赖方向:`selection → counting`(局部策略用 `_candidate_novel_rows`);
`counting` / `selection` / `records` 均不反向依赖 engine。**engine.py 对全部
迁出符号保留 from-import re-export**——`generation_service.py`、benchmark
harness 与 12 个测试文件的既有 import 位点一个未改;包 `__init__` 的惰性
`GenerationRunResult/RoundRecord` 导出路径(`.engine`)照旧生效。

未迁移(刻意):`_geometry_rejection_code` 与 `_finite_or_none` 只被 Engine
的 run 循环使用,留在 engine.py;快照 schema 常量(`_ROUND_RECORD_KEYS` 等)
与 fingerprint 助手是 engine 专属契约,不拆——把 run() 本体(约 500 行)或
snapshot 三方法抽成自由函数需要搬整个 self 状态面,收益低风险高,留给后续
(如与 MetricSpaceSpec/objective-schema 设计一起做)。

## 验证

- `tests/test_generation_selection.py` + `test_generation_engine.py` +
  **`test_generation_random_baseline.py`(黄金基线,accepted-set 签名逐位)**
  + `test_generation_resume.py` + `test_generation_artifacts.py` +
  `test_generation_screening.py` + `test_generation_objectives.py`:
  129 passed。
- 全量:`822 passed, 1 skipped, 0 failed`(拆分前基线 822/1/0)。
- `git diff engine.py`:−412 / +17(纯切除 + import/docstring,迁移代码
  逐字节原样;无一处行内修改)。

## 未决 / 后续

- 队列其余项:generation 侧 P1 七项(objective schema 提交期验证、
  `CoverageGainObjective(**_ignored)` 吞参、GeometryConstraints metadata、
  unscreenable_policy、relative energy window、cost-aware selection、
  coverage 术语统一)、MetricSpaceSpec、Acquisition pipeline、Task
  validation、诊断 3D-Viewer 跳转、指纹手性指数。注:P1 七项的评审原文
  未入库(docs/reviews 无对应文件),执行前需从代码重建意图或请用户补原文。
