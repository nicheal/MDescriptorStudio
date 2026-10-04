# gen-5 scientific metrics + GA/PSO local targeting — record (2026-10-03)

Milestone E2+E3 of docs/reviews/2026-10-02-improvement-plan.md, commit `8d10b3b`.
Gate condition (both sweeps archived) met: fps re-baseline 20261002T010733Z
(140/140, de-confound record in 2026-10-02-selection-strategy-sweep.md §5) and
PdCuNiP 20261002T005922Z (140/140, record 2026-10-03-pdcunip-sweep-analysis.md).

## E2 — gen-5 指标

- `GENERATION_ALGORITHM_VERSION` gen-4 → gen-5。gen-4 访问序计数**不变且继续上报**；
  已发布 R4 数值维持 gen-4 口径。历史 gen-4 预注册配置在 gen-5 契约下加载即拒（测试钉住）
  ——F 的新预注册以当前版本重写。
- `count_strict_unique_environments_v2`：与 gen-4 相同的贪心严格去重（对冻结档案、对已计入、
  候选内），但访问序规范化——候选按 candidate_id（2026-10-02 P0 稳定身份不变量）排序、原子行按
  字典序——计数成为选中行集合的纯函数。两种排列危险各由星形几何测试钉住（gen-4 在同一构造下合法地
  随访问序在 1↔2 摆动，v2 恒定）。规范化贪心仍是贪心：**非**最大间隔代表集、**非**连通分量聚类
  （2026-09-30 审计 §7.6 列出的两种替代定义，明确不采用；如需可再版本化）。
- RoundRecord 增 `strict_unique_v2` / `archived_strict_unique_v2`（默认 None → v3 快照与旧
  记录兼容加载）；pca discovery 载荷带 v2 汇总、archived 汇总、筛选拒绝总数；结果卡条件显示。
- `screening_bottleneck` 停机：筛选运行中归档发现率低于 min_novel_per_100_evals 而筛选前未饱和
  → 独立停机原因；描述符耗尽（原停机）优先判定。默认 0 关闭；轮循环与 resume 复查共用一份实现。

## E3 — GA/PSO 局部环境目标（G5 收尾）

- 解析门：target_mode=local_environment 放开 random/genetic/pso（原仅 random）。
- GA：池成员的局部锚点最小原子行距离（观测 local_descriptor，仅定向激活时由引擎填充）随池/快照
  持久化（必需字段，防"失忆恢复"）；定向轮盘票权乘 exp(-(d_local/r)²)——与 random 定向分支相同的
  乘法组合。无原子信号的成员因子为**精确 1.0**，非局部运行轨迹逐位不变。
- PSO：锚点拉力目标种子按 hypot(结构距离, 局部距离) 选取（无局部锚点/无原子信号回退结构距离）。
- 前端：payload 校验门移除（三优化器均支持），submission.test 钉住；后端 parse 门保持权威。

## 验证

后端全量 818 passed / 1 skipped / 0 failed（golden 基线不受影响——v2 为增量字段、非局部运行
逐位不变）；前端 vitest 252/252、eslint 干净；tsc 的 zh.ts "Points" 重复键报错来自同工作区
并行在途批次（diagnostics 跟进），本提交仅暂存 generation 侧 hunk。

## 遗留 / 后续

- F（冻结后再基准）：gen-5 冻结 → 碳 + PdCuNiP 新预注册（同 repeats/预算/目标/阈值/算子族，
  主指标可选 v2 口径）→ pre-screen 与 archived 两套发现率 → 发布包附数据集获取方式，关闭 L5。
- 连通分量聚类式口径若未来需要，须再版本化（gen-6），不得静默替换 v2。
