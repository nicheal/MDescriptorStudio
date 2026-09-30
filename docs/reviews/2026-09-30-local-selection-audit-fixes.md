# 局域选择审计修复记录（2026-09-30）

输入:`docs/plan/MDescriptorStudio_Local_Selection_Audit_2026-09-30.md`(审阅 main @ `9f73f0b`)。
本次按其 §7.8 修复顺序落地了全部代码级修复;工作区改动,未提交(等待用户指令,提交时须 `git add -f docs/`)。

## 改动清单(全部在 `backend/.../generation/engine.py`)

### 1. P0——计数器统一到 archive 缩放空间(审计最高优先级)

`count_strict_unique_environments` 此前把 raw 行传给共享过滤器:`nearest_per_row` 内部缩放,但"已计入集"的批内比较留在 raw 空间——与局域选择器(显式 scaled)、阈值定义空间不一致。尺度 > 1 过计数、< 1 欠计数(审计案例 A),benchmark numerator 与 discovery_saturated 停机同受影响。

修复(即审计建议的最小修复):入口 `apply_scaling(local_archive.scaling, atomic_values)` 一次;冻结档案掩码继续用 **raw** 行查询(`nearest_per_row` 内部自缩放,严禁传入 scaled 行造成二次缩放);共享过滤器 `_candidate_novel_rows` 与 counted 集全部存 scaled 行。修复后选择器与计数器才真正"共用同一距离空间"——`select_local_incremental_batch` docstring 中 "exactly the count_strict_unique_environments semantics" 由近似变为精确。空间契约写入两个函数的 docstring。

### 2. P1——local/FPS elite 排序统一(审计案例 F)

FPS:`argsort(fitness, stable)[::-1]`(同分取**后**输入序);local:`argsort(-fitness, stable)`(同分取**前**输入序)。同分组内预算/池截断使两策略选出不同身份,破坏公平对照。

修复:提取模块级 `_fitness_elite_order(fitness, finite, pool_size)`,FPS 调用处表达式逐字保留(行为逐位不变);local 改用同一 helper。**选择不反向修改 FPS**:golden baseline 钉住 FPS 选择顺序(经 selection_rank 进反馈池),且 R4 预注册重跑正在进行——改变 FPS 历史顺序须按矩阵 §1 重产生基线并记录策略版本,非本次低风险范围。副作用:全同 fitness 时 local 首选从输入首行变为末行,`test_local_strategy_resolves_what_mean_pooling_hides` 的身份断言 {0,2} → {2,3}(仍是"绝不选环境重复 c1"的原意)。

### 3. P1——等预算接收数契约(审计案例 J)

`max_candidates=128` 的精英池上限会把池截断到预算以下:129 个候选、budget=129 时 local 接收 128、FPS 接收 129,静默违反"零增量填满同等预算"。修复:池上限项从 `max(1, max_candidates)` 改为 `max(budget, max_candidates)`——上限只裁剪精英**盈余**,永不裁到预算之下。budget ≤ 128 时 pool 组成逐位不变;仅 budget > 128 的场景(此前是坏行为)发生变化。

### 4. P1——全空局域行批次不再使引擎状态不一致(审计案例 H 引擎路径)

所有被选结构的原子行段为零长度时:结构档案已 add,随后 `LocalEnvironmentArchive.add` 对 shape[0]==0 抛 ValueError——异常路径留下已接受但局域档案缺失的不一致状态。修复:更新前检查 `rows.shape[0]`,空批次跳过 local add(语义:零环境结构对环境档案贡献为零),轮次正常完成。部分空批次行为不变。

### 5. P1——顺序依赖契约文档化(审计 §5-P1,不修码)

阈值近邻不是等价关系,"严格唯一环境数"是**给定访问顺序(选择序 → 行序)的贪心严格间隔代表数**,不是排列不变量——审计明确此性质无法靠改 `>` 或统一缩放消除,排列不变需要稳定候选身份 + 规范化行序 + 版本化重定义(升 gen-5)。已写入 `count_strict_unique_environments` docstring 并由回归测试钉住;矩阵 §4 记录该契约。未排期实现。

## 明确不做(与审计口径一致)

- **P2 性能**(逐行最近距离重复扫描、非精英候选的 scaling/查询):审计本身"未测性能,仅列源码风险";改动需缓存与预分配重构,非低风险。留给后续带性能门槛的批次。
- **FPS 排序反转的"规范"化**(同分取前输入序):同上第 2 条,涉及基线重产生。
- **案例 E(局域可输 FPS 的对抗数据)**:属算法外推边界,现有 5-seed 固定 benchmark 验收口径保留。

## 测试

`tests/test_generation_selection.py`:
- `test_local_strategy_reports_what_it_optimizes` 重写:独立 scalar 循环 scaled oracle(`_scaled_oracle`,直接按定义实现)与计数器**精确相等**,robust 缩放 + 随机数据覆盖候选内/跨候选两分支(原断言仅 `unique > 0`,审计 §7.1 点名的最直接缺口)。
- 新增 `test_budget_above_the_elite_cap_still_fills_like_fps`(案例 J:129/129 与 FPS 等接收)。
- 新增 `TestEliteTieContract::test_equal_fitness_elites_match_the_fps_baseline`(案例 F:两种输入排列下 local 与 FPS 同身份)+ `test_empty_reference_archive_rejected_empty_accepted_supported`(案例 H 契约区分)。
- `test_local_strategy_resolves_what_mean_pooling_hides` 身份断言随共享 tie 序更新。

`tests/test_generation_engine.py`:
- 新增 `test_counter_counts_in_the_archive_scaled_space`(案例 A 双方向 × 候选内/跨候选 4 例)。
- 新增 `test_visit_order_is_part_of_the_metric_contract`(案例 B+C,钉住文档化契约)。
- 新增 `test_discarded_duplicate_rows_still_enter_the_next_round_archive`(§7.6:同轮丢弃行经正式更新成为下轮参照)。
- 新增 `TestArchiveUpdateContract`:`test_local_archive_updated_once_per_round_with_frozen_queries`(§7.5:spy 锁定每轮恰好一次 local add、轮内查询全部看到冻结档案)+ `test_all_empty_atom_rows_complete_without_local_add`(案例 H 引擎路径)。

## 验证

- **红-绿**:仅回退 engine.py(old 实现 + 新测试)→ 6 个行为修复测试失败(缩放空间、oracle 相等、预算 129、tie 一致、mean-pooling 身份、空批次崩溃),4 个契约钉住测试(旧实现即正确)通过;恢复修复后 44/44 全绿。
- **全量 backend**:`641 passed, 1 skipped, 5 failed`——5 个失败与本机已知既有环境失败清单逐项一致(hdbscan ×2、descriptor_flow ×2、backend_smoke),非回归;633 + 8 个新测试 = 641。golden baseline(`generation_random_baseline.json`)通过——FPS 路径逐位未动的直接证据。
- 前端无改动,未重跑。

## R4 重跑口径说明

R4 预注册 sweep(`benchmark/results/20260929T043150Z`)已在修复前代码下自然完成(140/140,记录 `docs/reviews/2026-09-30-r4-preregistered-sweep.md`):其进程加载的是本修复之前的代码,全部 unique 数值为 strict-but-raw 空间旧口径,组内配对比较自洽。本修复全部在工作区、未随之提交,与该 sweep 无交集。此后所有运行(含本修复提交之后)的 unique_novel_environments 为缩放空间新口径:与 R4 数值对比时须按矩阵 §1/§4 注明口径分界,不得直接混排。
