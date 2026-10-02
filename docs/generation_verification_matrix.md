# Generation 验证矩阵（R0）

- 建立日期：2026-09-29
- 基准提交：`e8797c8`（审计基准）；本矩阵落笔时工作区已含审计修复批次（P0-01/02、P1-01/02/03、P0-03/04、P2-01、P1-05 代码层，详见 `docs/reviews/` 提交记录）
- 审计文档：`docs/plan/MDescriptorStudio_Generation_Deep_Audit_2026-09-29.md`
- 目的：区分五类验证层级各自能证明什么、不能证明什么；固定"指标版本 / 策略版本 / 基准版本"三个显式字段，防止历史结果与新结果混排（审计 R0 验收门）。

## 1. 版本字段（引用任何结果时必须三者齐备）

| 字段 | 当前值 | 语义变化时必须升级 |
|---|---|---|
| 指标版本（算法版本） | `GENERATION_ALGORITHM_VERSION = "gen-4"` | 持久化指标语义变化（如发现率停止改读严格唯一计数之类**改变停止行为/指标口径**的改动）。审计修复批次中 P0-02/P1-01 只收紧了 unique 指标计数与停止条件读取口径——`unique_novel_environments` 的字段语义（"冻结档案 + 本轮已计入去重"）自 A12 引入即未变，变化的是计数正确性（修复高估/低估），故 gen-4 维持；重新引用历史 unique 数值时须注明"修复后重算" |
| 策略版本 | `selection_strategy = structure_fps_v1`（默认）\| `local_incremental_maximin_v1` | 批内选择语义变化。两策略共用同一严格计数定义（`count_strict_unique_environments`） |
| 计数口径 | `metric_caliber = "strict-unique-scaled"`（当前引擎，2026-09-30 缩放空间修复起，基准 harness 未启用能量筛选，故不受 §5 筛选计数分界影响）；历史发布值 `"strict-unique-raw"`（修复前代码，含已发布的 R4 包 2026-09-30-r4） | 计数比较空间变化。2026-10-01 起预注册必须声明口径并在加载时强校验（不符即拒绝），`environment.json` 携带 harness/口径/算法版本戳，逐 run 行携带 dataset_id/descriptor_run_id——跨口径或跨材料数值一律不得混排 |
| 基准版本 | harness 2026-10-01（预注册完整合同校验 + dataset↔run/fingerprint 门 + 锚点几何预检 + 预算合同 max_accepted/max_generations 入注册；此前 2026-09-29 为 P0-03/P0-04 修复后） | 锚点角色、种子池装配、半径口径、注册合同变化。**修复前的全部归档 sweep（20260925T042355Z 及以前、20260925T090608Z、20260925T133939Z、20260925T151421Z、20260926T024832Z）均产生于"锚点传给所有优化器 + within_radius=1.0"的旧 harness**，其 unique/coverage 数值仍可用于当时声明的对照，proximity 数值仅 target_region/genetic-target/pso-target 组可解释，random 组的"无定向"声明不成立（P0-03） |

## 2. 五层验证矩阵

| 层级 | 载体 | 能证明 | 不能证明 | 当前覆盖 |
|---|---|---|---|---|
| L1 Python 单测 | `tests/test_generation_*.py`、`tests/test_benchmark_harness.py` | 纯函数语义（严格计数、选择策略、轮盘分布、请求校验、几何约束）、红/绿回归 | 真实描述符质量、真实数据集上的科学结论 | 600+ 用例；golden 基线（`tests/data/generation_random_baseline.json`）钉住无定向 Random 逐位不变 |
| L2 worker 端到端（stub 评估器） | `test_generation_engine.py`（stub evaluator 全流程）、`test_generation_ipc.py`（真实后进程 IPC） | 引擎装配、生命周期、工件完整性、缓存键、freshness 门 | NEP 特有行为 | IPC 生命周期测试覆盖 submit→run→preview→materialize |
| L3 Playwright mock E2E | `frontend/e2e`（mock 后端） | 前端布局、交互、禁用联动 | 后端正确性 | 生成页配置/运行/结果导航 |
| L4 真实 NEP 基准 | `benchmark/genetic_vs_random.py`（本机数据） | 固定种子下的配对比较（每 100 次评估严格唯一数、coverage 半径、proximity） | 任何超出该数据集/描述符的普适结论；未经 R4 预注册重跑的论文级结论 | harness 已修复锚点角色/半径；**重跑前不产出新结论**（见 §4） |
| L5 独立复现 | 第三方按 §3 环境事实 + 发布的逐 seed 数据重绘 | 论文级可复现性 | — | 未开始（依赖 R4/P1-06 数据发布决策） |

## 3. 环境事实（L4/L5 引用时逐项核对）

- OS：Windows 10.0.19045（win32），CPU 32 逻辑核（harness `WORKERS = cpu_count`）
- Python：见 results 目录 `environment.json`（每次 sweep 落盘）
- 数据集：`ds_d56748fb4391`（carbon），6738 帧 extxyz，fingerprint `v4:446a981b307a6a23f0e50ad1e211524c1986bb8b6d711ae6aad1b0af9523e0cf`
- 描述符运行：`run_57a8b8c40286`（NEP，atom 级 35 特征，device=cpu，status COMPLETED）
- 第二材料（2026-09-30 注册，2026-10-01 外部审阅时本地核实绑定）：`ds_9ca89d14f8f0`（PdCuNiP 金属玻璃，9615 帧，多组分 Pd/Cu/Ni/P，每帧原子数中位 106、范围 2-256），fingerprint `v4:c3a81d61cb69c78e2a6845f11a4969b8f3d02cef87b887e7a6897f83fa94b5eb`；描述符运行 `run_644f6186340c`（NEP，atom 级 35 特征，[736798,35]）。核实项（2026-10-01）：run.dataset_id 与 dataset 一致、结果 metadata.dataset_fingerprint 与 dataset 当前指纹一致、文件指纹重算一致、values 全有限、row_offsets 单调且末端对齐、无零方差特征
- 几何约束：`min_distance_mode=covalent, factor=0.7`（自 2026-09-29 起含自镜像检查——P1-05；此前的 L4 结果产生于无自镜像检查的过滤器，候选通过率可能略有差异）
- 种子：repeat i → seed 1000+i（20 repeats）；锚点帧——carbon 为 1322,5075，**PdCuNiP 为 2256,2133**（2026-10-01 起预注册；原样拷贝的 carbon 索引在该材料不可用：1322 未通过运行几何约束预检，5075 落在密集核心——7719/9615 帧在其 r=15 邻域内，而 carbon 上同索引仅 21/6738；替代帧为最具区分度的几何合法体相帧，各 54 原子，最近云帧 ≈27 缩放单位，两锚点相距 >40）；`REGION_RADIUS=15.0`（robust-scaled 单位，非 Å）。单次 displacement 扰动（σ≤0.15 Å）的描述符位移在同批探针下两材料量级相当（carbon 1.1-9.1，PdCuNiP 0.65-8.7 缩放单位），r=15 大于扰动步长的设计在两材料均成立；原子级阈值 0.25 在两材料均处于有响应区间（采样 NN<0.25 比例 ≈0.6-0.8%）

## 4. 已知既有失败与结果混排禁令

> **R4 预注册重跑已完成(2026-09-30)**:`benchmark/results/20260929T043150Z/`,140/140,记录见 `docs/reviews/2026-09-30-r4-preregistered-sweep.md`。引用发现/定向结论一律以该记录为准;同日 GUI 中 GA/PSO/reuse 的基准提示文本已按 R5.6 更新。

- 后端套件在本机有 5 个**与 generation 无关**的既有环境失败（hdbscan/analysis_ipc/backend_smoke/descriptor_flow×2），在干净 HEAD 上同样失败——不作为回归信号。
- 审计修复批次落地前的全部 L4 数值（含三份 docs/reviews 记录中的表格）为历史记录；R4 预注册重跑（`benchmark/config.json` 冻结场景）完成前，不得将新旧数值混入同一图表或结论。
- P1-04 裁定（2026-09-29）：轮盘实现的边际权重 (m−r)² 为 USPEX 忠实移植（累计表 + 阈值抽样的边际即相邻表项之差），docstring 表述已修正，分布检验钉住平方剖面；实现未变 → gen-4 不升版。若未来改用累计票数（立方压力），须升 gen-5 并重做 L4。
- 2026-09-30 局域选择审计批次（`docs/plan/MDescriptorStudio_Local_Selection_Audit_2026-09-30.md`；完成记录 `docs/reviews/2026-09-30-local-selection-audit-fixes.md`）：
  - **P0 计数空间统一**：`count_strict_unique_environments` 的批内比较（已计入去重、候选内去重）从 raw 空间移入 archive 缩放空间（与阈值、局域策略同空间；archive 掩码仍以 raw 行查询，避免二次缩放）。字段语义（"冻结档案 + 本轮已计入去重"）不变，属计数正确性修复 → gen-4 维持；**所有 2026-09-30 修复前产生的 unique_novel_environments 数值（含已完成的 R4 重跑 20260929T043150Z，其进程加载的是修复前代码）在引用时须注明"缩放空间修复前口径"**，不得与修复后运行直接混排。
  - **P1 策略口径**：local 策略的 fitness elite 排序改为与 FPS 共享 `_fitness_elite_order`（稳定升序反转 = 同分取后输入序；FPS 历史顺序逐位不变，golden 基线不受影响）；`max_candidates` 精英上限不再把池截断到预算以下（等预算接收数与 FPS 一致，审计案例 J）。两策略版本号维持 `local_incremental_maximin_v1` / `structure_fps_v1`——口径修正记录于本行，跨版本比较局域策略结果时注意分界。
  - **P1 边界**：全空原子行批次跳过 local archive 更新（此前在结构档案更新后抛 ValueError，状态不一致）。
  - **顺序依赖契约（不修码）**：严格计数 = "给定访问顺序（选择序 → 行序）的贪心严格间隔代表数"，非排列不变量；已写入 docstring 并由 `test_visit_order_is_part_of_the_metric_contract` 钉住。排列不变指标需要稳定候选身份 + 版本化重定义（升 gen-5），未排期。
- 2026-10-01 外部审阅批次（PdCuNiP 预注册审阅，回应记录 `docs/reviews/2026-10-01-pdcunip-review-response.md`；harness 升版 2026-10-01）：
  - **resume 材料护栏**：`resume_sweep.py` 此前从模块默认 ID 解析数据绑定——对第二材料 sweep 恢复会补跑 carbon 行并混入同一张表。现恢复前强制：① 预注册 ID 经 `apply_preregistration` 注入 harness 模块；② 校验目录内冻结的 `config.used.json` 与给定配置一致（后加的注记键与 note 除外）；③ 逐行核验 dataset_id/descriptor_run_id（2026-10-01 前的无身份行仅对默认 carbon 配置可续）。混合材料恢复在装载即被拒绝。
  - **预注册完整合同**：`_load_preregistration` 此前只查键存在（`primary_metric="wall_seconds"`、`algorithm_version="unsupported"`、`repeats=0`、`groups=["nonsense"]` 等均能通过加载）。现加载时校验：metric_caliber（口径不符 = 拒绝，杜绝 raw/scaled 混排）、algorithm_version == 引擎版本、harness_min_version ≤ 当前、selection_strategy/primary_metric/secondary_metrics/groups/primary_groups 取值合法、repeats/budget/seed_base 整型下界、max_accepted/max_generations 与 harness 预算上限一致、dataset_id/descriptor_run_id 存在。
  - **数据绑定门**：`_load_run_config` 现校验 run.dataset_id == 预注册 dataset、结果 metadata.dataset_fingerprint == dataset 当前指纹（与提交/worker 的 freshness 门对齐）。
  - **锚点几何预检**（worker 同款）：harness `run_once` 现在锚点越界或违反运行几何约束时 fail fast——PdCuNiP 拷贝锚点 1322 即因此被替换（见 §3）。
  - **预算合同**：`max_accepted=500`/`max_generations=10000` 从 run_once 字面量提取为 harness 常量并入预注册（config.pdcunip.json）；工件（config.used.json/run_results.jsonl/summary 等）显式 LF 写盘，发布校验不再受换行转换影响。
  - **进行中 sweep 的口径警示**：selection-strategy sweep（20260930T101403Z，scaled 口径）与 fps 基线（20260929T043150Z，raw 口径）的跨 sweep 配对 unique 差值**混杂了计数口径与选择策略两个因子**——引用时必须带口径注解，或先在修复后代码上重跑 fps 基线再下策略结论。

## 5. 用户决策记录

> **用户决策（2026-09-30）**：① P1-06 原始 extxyz 数据暂不发布，留待以后——仓库内汇总包
> （`benchmark/published/2026-09-30-r4/`，配对分析可独立复算）即为现行发布形态；② R5.1 完整状态机
> 与能量/力筛选接口**等待 mdescriptor 引擎支持能量和力预测**后再设计，当前以 accepted.extxyz 的
> 显式状态字段（geometry_passed/descriptor_novel/energy_screened=false/train_set_ready=false）为准。
> **更新（2026-09-30，引擎 0.3.5 提供 NEP/DPA4C 能量/力预测器后）**：R5.1 已实现——
> `generation/screening.py`（NEP/DPA4C、CPU/CUDA、逐候选三态判定 pass/fail/unscreenable），
> 引擎在选择后、归档前做第二阶段筛选（`rejected_screening` 入轮记录与观测），accepted.extxyz
> 状态字段动态翻转并记录 energy_per_atom/max_force；解析/提交双端校验，UI 约束卡可配置。
> DPA4C 需显式 checkpoint；部分周期候选不做筛选（不静默拒绝）。
> **更新（2026-10-01，R5.1 外部审计修复批次，`docs/reviews/2026-10-01-r51-audit-fixes.md`）**：
> ① 上面"状态字段动态翻转并记录 energy_per_atom/max_force"至此才真正落地（此前 writer 固定写
> energy_screened=false）；`train_set_ready` 语义 = 携带测量值**且**配置了至少一个上限（纯测量
> pass 不作可训练宣称）。② `novel_environments` / `unique_novel_environments` 两项发现指标改为
> 按筛选**前**选择集计数（轮记录 docstring 的既有契约），新增 `archived_unique_novel_environments`
> 记录实际归档口径；discovery_saturated 停机读"发现"口径——严格筛选不得伪造饱和。修复前后的
> unique 数值在引用时须注明口径分界（与上面 2026-09-30 缩放空间分界同理）。③ PSO 记忆排除
> screening_rejection 候选（不排除全部未接收）；快照/恢复保留能量与力测量值；capability 门从
> "模块存在"加深到"predictor 类 + native DPA4C 可解析"；ScreeningSpec 校验 checkpoint 类型并
> trim；适配层拒绝非有限预测（unscreenable）；`requirements.txt` 下限升 0.3.5；启用筛选的运行
> 将 mdescriptor 版本折叠进 cache key。DPA4C 0.3.5 起有内置默认模型（Air-OMat24），显式
> checkpoint 仅为可选覆盖。
> **更新（2026-10-02，外部审查 P0/P1 修复批次，计划与执行记录 `docs/reviews/2026-10-02-improvement-plan.md`，提交 `f526a61`）**：
> ① **候选 ID 唯一性不变量**：strain/shear ID 追加形变矩阵内容摘要后缀（同 |ΔV| 不同形变不再可能碰撞；
> 无 RNG 消耗，搜索轨迹与 golden 基线数值逐位不变，基线仅重镕 9 个 ID 字符串）；引擎运行时强制全 run
> 唯一（碰撞提案原位改名 `<id>_x<k>`，对象身份保留），restore 回填已发放 ID 集，快照 accepted 表加硬断言。
> ID 格式变化**不属于指标语义**——`unique_novel_environments` 等口径与停止行为未动，gen-4 维持；
> 旧工件中的旧格式 ID 仍可读（ID 为不透明字符串，无解析依赖）。② **快照指纹收紧**：并入
> GeometryConstraints 全量、evaluator 身份（`DescriptorEvaluator.signature()`：描述符名/参数/adapter/
> device/mdescriptor 版本）与 screening 模型内容身份（`model_identity()`：解析资源 sha256 + 包版本）——
> 此三者的历史快照（如有）将被指纹拒绝，属刻意的兼容性破坏。③ **缓存键**：显式 screening checkpoint
> 以文件内容 sha256 入键（原地替换模型不再复用旧缓存）；`metadata.json` 增 `screening_model_identity`。
> ④ **SSW/外部优化器正式移出范围**（用户决策 2026-10-02）：原 G5 规划的 ExternalGenerationAdapter/SSW
> 不再排期，G5 以"局部目标搜索覆盖 GA/PSO"收尾；本行即该决策的记录载体。⑤ 锚点增强语义获确认保留
> （锚点可位于所选种子视图之外并并入种子池；前端提示文案已披露），种子范围汇总行的显性化归入 gen-5 前的契约澄清。
> **更新（2026-10-02，resume 产品化批次）**：引擎快照升 **v3**（evaluated 候选记录作为第三个带哈希数据文件
> 入快照——resumed 运行重发布的描述符空间地图完整；v2 快照加载即拒，无生产快照故无兼容负担）。服务层接入
> R5.5：worker 每轮边界持久化快照至 `generation_snapshots/<id>`，新增 `INTERRUPTED` 状态（重启/关闭僵尸清扫
> 中"RUNNING 且有快照"的运行改标之，其余仍 CANCELLED）与 `generation.resume` RPC（restore_state 全指纹校验，
> dataset 重导入/描述符重指/checkpoint 换字节/约束改动均拒绝）；验收测试 = 硬杀后端→重启→resume→与不间断
> 固定种子参照运行轮次记录逐字节一致（`test_generation_ipc.py::test_generation_resume_after_interruption`）。
