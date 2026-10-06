# Objective schema 提交期验证 + CoverageGainObjective 吞参修复(2026-10-06)

P1 清单前两项(gen-5 冻结后解锁)。同批完成,共享一个动机:**无效 objective
不再排队后失败**——optimizer 参数自 A10 起就有提交期验证,objective 一直没有。

## 现状与问题(修复前)

- `models.parse_request` 对 objective 只查 `type` 是否在注册表;其余键原样
  透传,直到 worker 的 `registry.build_objective` 才以构造器行为收口:
  - `NoveltyObjective()` 无构造参数 → 任何多余键 = worker 期 `TypeError`
    (排队成功、执行失败,UI 只见 job FAILED);
  - `CoverageGainObjective(**_ignored)` 显式吞掉一切多余键(注释称"向前
    兼容") → 拼错的键静默无效;
  - local/composite 构造器有范围检查,但同样只在 worker 期触发。
- 佐证:仓库测试里就有 `{"type": "novelty", "aggregation": "mean"}` 这种
  永远跑不通 worker 的载荷(test_generation_selection/service_gates 的
  fixture),因为测试从不执行 worker 所以从未暴露。

## 实现

- **`objectives/__init__.py`:`validate_objective_params(objective)`** —
  schema 真值放在构造器同包:`OBJECTIVE_PARAM_KEYS` 声明每类的可调键
  (novelty/coverage 零参数;local 4 键;composite 4+2 键),`type` 与
  `scaling` 全类型接受(scaling 选 archive 缩放模式,由 service 消费,
  构造器收不到;合法值 = `analysis.sampling.preprocessing.SCALING_MODES`)。
  值检查与构造器逐一对应、措辞一致:aggregation ∈ AGGREGATIONS(公共别名,
  local_diversity 新增)、top_fraction/quantile ∈ (0,1]、novelty_threshold
  为正有限数或 null、composite 双权重非负且和 > 0(缺省键走构造器默认,
  只有双零显式给全才拒)。数字检查沿用 P2-01 严格风格(bool 拒、字符串拒)。
- **`models.parse_request`**:注册表类型检查之后调用
  `validate_objective_params`,`ValueError → AppError(INVALID_PARAMS)`
  (与 registry KeyError 同款映射;惰性导入与 registry 一致,不引入新环)。
- **`coverage.py` 构造器**:`__init__(**_ignored)` → `__init__()`,注释更新:
  向前兼容是提交期 schema 的职责,漏网键在这里是响亮的 TypeError 而非静默。

## 兼容性

- 前端 submission.ts 能发出的四种载荷(novelty/coverage/local/composite)
  全部逐键通过(`test_every_ui_payload_shape_passes` 钉死);前端零改动。
- resume 重解析 `params_json`:已完成 run 的存储载荷只可能携带合法键值
  (构造器本就在 build 期拒绝其余),IPC resume 契约测试通过。
- 两处测试 fixture 的无效载荷(`novelty + aggregation`)按新契约修正
  (它们本就是"排队后失败"的活例)。

## 验证(红绿)

- 新测试 `tests/test_generation_models.py::TestObjectiveSchema` 6 项
  (逐类型未知键、scaling 合法值、local 参数范围 10 例、合法载荷、composite
  权重、UI 全形状)。红:摘除 models 接线(stash)后 4 个拒绝类测试失败、
  2 个合法类通过;接回后全绿。
- 全量后端 **828 passed + 1 skipped + 0 failed**(拆分前基线 822 + 新增 6;
  黄金基线逐位一致在内)。

## 未决

- P1 清单剩余五项(GeometryConstraints metadata→几何重算、
  unscreenable_policy、relative energy window、cost-aware selection、
  coverage 术语统一)仍缺评审原文,属行为/功能设计项,需逐项从代码重建
  意图或由用户补原文后再动。
