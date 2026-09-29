# MDescriptorStudio 生成采样扩展：代码审阅与后续实施计划

- 审阅日期：2026-09-29
- 仓库：https://github.com/nicheal/MDescriptorStudio
- 基准分支：`main`
- 基准提交：`e8797c810a53b5bb17486e7afcc4dff6da2a3a00`（2026-09-28）
- 审阅方法：通过已连接的 GitHub 读取默认分支树、实现文件、关键测试、Benchmark、计划和开发记录，并核对该提交的 GitHub Actions 状态。由于本执行环境无法连接 GitHub 进行本地克隆、不能取得项目本地碳数据集及 NEP 结果，此报告是**源码静态审阅 + 仓库 CI 状态核验**，不是本地重跑 20×10000 描述符评估的独立实验报告。
- 最新提交 CI：https://github.com/nicheal/MDescriptorStudio/actions/runs/36401154196 （GitHub 返回成功）。CI 目前**未**执行完整 generation 科学 Benchmark。
- 文档与源码发生分歧时，以当前提交源码行为为准。历史测量记录仅作为历史记录，不直接视为当前代码的独立复现结果。

## 1. 总体结论及 G0—G5 状态

从 9 月 25 日的 G3.5 之前状态至当前提交，工程实现有实质推进：独立 `generation/`、按批次生成-几何过滤-计算-目标评价-归档-反馈闭环已形成；Random、mutation-only GA、memory-PSO 均可构建；目标区域作为独立请求字段已组合到三个优化器；工件存储、运行进度、结果图、保存/追加数据集和自动化单元/E2E 测试均有代码。G3.5 原有 coverage 方向、accepted-only radius、local run 限制、优化器生命周期等整改可从源码核实。

| 阶段 | 当前代码 | 深度与不足 |
|---|---|---|
| G0：基础重构 | 已实施 | 共享距离核、强类型模型、注册表存在；需要跨模块契约的回归测试。 |
| G1：Random Expansion | 已实施 | 非定向基线有 golden fixture；定向分支存在错误参数传递。 |
| G2：局域环境扩展 | 已实施 | 原子矩阵、row_offsets 和局域目标保留；唯一环境计数不严格，最终选择依赖结构级 FPS。 |
| G3：Iterative Maximin | 部分至主体实施 | top-fitness 候选池内 FPS、coverage objective、发现率停止已实施；并非严格 archive-warm-start/批内边际增益贪心。 |
| G3.5：正确性与接口 | 大部分已实施 | 原先 A01—A12 多项已有修复，但新发现了独立科学指标和控制流问题。 |
| G4：mutation-only GA | 已实施并有实验记录 | Genome、rank roulette、移民、observe/反馈、基准脚本均存在；公式与实现注释有偏差；历史结果需使用修复后的脚本再确认。 |
| G5：memory-PSO、定向目标 | 已实施并有实验记录 | pbest/gbest 与定向拉力已存在；与区域目标函数冲突、Benchmark 对照不一致；PSO 并非标准连续速度 PSO，命名和论文中应准确说明。 |
| External/SSW | 非当前承诺范围 | 仓库开发记录指出该项已取消；若重新决定研究真实能量面搜索，再另立独立研发范围。 |

**阶段判定与科研就绪度必须分开**：`main` 已达到“可运行的研究原型”，但当前 Benchmarks/局域环境唯一性指标不能直接支撑定量方法优劣的论文结论。

## 2. 缺陷清单（按修复顺序）

### P0-01：定向 Random 错误传递算子参数

- **源码**：[`random_search.py` `_propose_targeted()`](https://github.com/nicheal/MDescriptorStudio/blob/e8797c810a53b5bb17486e7afcc4dff6da2a3a00/backend/mdescriptor_studio_backend/generation/optimizers/random_search.py#L169-L185)。
- **现象**：`_apply_uniform` 遍历可用算子时循环赋值 `params`，随机选定 `operator` 后却调用 `operator.apply(parent, rng, params)`。变量 `params` 属于最后遍历算子，不一定属于选中的算子。若默认值不同，可能悄悄改变用户设置的位移/应变/剪切幅度。
- **修复**：调用时从**选定** `operator` 获取 `operator.operator_params`；尽量复用非定向分支的公共 `_choose_and_apply`，避免两套分支再次分叉。
- **测试**：准备两个 spy operators，其 `operator_params` 分别为明显不同值，固定 RNG 保证选择第一个与第二个各一次；断言真实 `apply` 参数与选择一致。增加单算子、多算子与 `can_apply=False` 分支测试。
- **重要性**：原先的定向随机 Benchmark 包含多种算子；该缺陷修复前的搜索分布不是严格的宣称配置。

### P0-02：`unique_novel_environments` 非严格唯一计数

- **源码**：[`engine.py` 第 214—243 行](https://github.com/nicheal/MDescriptorStudio/blob/e8797c810a53b5bb17486e7afcc4dff6da2a3a00/backend/mdescriptor_studio_backend/generation/engine.py#L214-L243)。
- **问题 A：候选内部重复**：同一个结构中两个彼此距离低于阈值、但都远离 frozen archive 的环境均被计数。
- **问题 B：非新环境污染临时集合**：当前结构所有 `rows` 都被 `counted.append(rows)`，包括对 frozen archive 不新颖的环境；它们可排斥下一结构对 frozen archive 确实新颖的环境。
- **建议语义**：按固定的结构选择次序和固定原子行次序，将每条原子描述符 `z` 先与 frozen archive 比较，再与**已经计数的新颖环境**比较；两次距离都严格大于阈值时才计数，并将该行加入新环境集合。该集合贯穿整轮，前轮已经接受的环境通过正式 local_archive 参与下一轮；不把非新颖原子行加入临时集合。
- **反例测试 1**：原始参考 `[0]`，阈值 `1`，单结构局域行 `[3.0, 3.0]`：strict unique 应为 `1`，当前实现为 `2`。
- **反例测试 2**：参考 `[0]`，阈值 `1`，第一结构行 `[0.9]`，第二结构行 `[1.8]`：strict unique 应为 `1`，当前实现可能为 `0`。
- **边界**：刚好等于阈值、空块、多个结构同一区域、行顺序、初始归档只有一个环境、不同缩放方式；明确 `>` 与 `>=` 的契约。

### P0-03：Benchmark 基线意外开启定向逻辑

- **源码**：[`genetic_vs_random.py` run_once/main](https://github.com/nicheal/MDescriptorStudio/blob/e8797c810a53b5bb17486e7afcc4dff6da2a3a00/benchmark/genetic_vs_random.py#L134-L230)；[`random_search.py` `_targeting_active()`](https://github.com/nicheal/MDescriptorStudio/blob/e8797c810a53b5bb17486e7afcc4dff6da2a3a00/backend/mdescriptor_studio_backend/generation/optimizers/random_search.py#L124-L126)。
- **现象**：Benchmark 默认 `--anchor-frames=1322,5075`，在 `run_once` 中将非空 `anchor_descriptors` 传给所有优化器，**即使标签为 `random`**；而 Random 在收到非空锚点时立即走定向分支。因此当前 `random` 标签不必然是无定向的 Random。
- **修复**：将 `metric_anchors`（仅测量用）和 `search_anchors`（决定策略的输入）彻底分开。严格无定向基线的 `OptimizationContext.anchor_descriptors=()`；距离评价器独立使用 `metric_anchors`。设置运行结果字段 `targeting_enabled` 并运行时断言。
- **测试**：相同种子池/运算预算下，无定向 Random 有无 metric_anchors 时提案签名必须逐位一致。

### P0-04：Benchmark 的目标半径和强制锚点逻辑与 worker 不一致

- **源码**：[`genetic_vs_random.py`](https://github.com/nicheal/MDescriptorStudio/blob/e8797c810a53b5bb17486e7afcc4dff6da2a3a00/benchmark/genetic_vs_random.py#L164-L260)，[`generation_service.py` 强制锚点](https://github.com/nicheal/MDescriptorStudio/blob/e8797c810a53b5bb17486e7afcc4dff6da2a3a00/backend/mdescriptor_studio_backend/services/generation_service.py#L410-L472)。
- **问题 A**：半径统计通过 `params.get('region_radius', 1.0)` 获取参数，但单独定义的定向半径变量未进入 `params`，使 `within_radius` 以 `1.0`（而非实验设置的 `15.0`）计数。
- **问题 B**：脚本只对 `target_region` 强制插入锚点，然而 worker 对**所有**有锚点的策略强制插入。这破坏了 `genetic-target` 和 `pso-target` 与产品运行路径的一致性。
- **修复**：定义 `BenchmarkScenario` 或共享 worker assembly；统一生成种子池。比较**算法在相同可用父代资源下的行为**时，所有组获得相同完整 seed_pool，只有开启定向策略的组得到 `search_anchors`；全部组均可使用 `metric_anchors` 统计距离。用独立的 `region_radius` 变量计算所有组同半径的比例。
- **要求**：历史文档中 20×10000 的数值保留为历史记录；修复后重新执行，不将旧数值覆盖或与新指标混合绘图。

### P1-01：发现率停止条件仍使用重复计数

- **源码**：[`engine.py` 第 531—549 行](https://github.com/nicheal/MDescriptorStudio/blob/e8797c810a53b5bb17486e7afcc4dff6da2a3a00/backend/mdescriptor_studio_backend/generation/engine.py#L531-L549)。
- **现状**：G3.5 已增加 `unique_novel_environments` 供 Benchmark，但 `discovery_saturated` 仍对 `novel_environments` 原始和求发现率。重复环境可以阻止本应发生的饱和停止。
- **修复**：统一“停止/基准/结果 UI”读取同一版本化 unique 指标；保留 raw 作为诊断。`generation_service.pca` 当前汇总 `candidates.jsonl` 的 raw `novel_environment_count` 并显示为 Novel environments，须同步改名或改算。

### P1-02：数据集 freshness 校验存在窗口

- **源码**：[`generation_service.py` submit/worker](https://github.com/nicheal/MDescriptorStudio/blob/e8797c810a53b5bb17486e7afcc4dff6da2a3a00/backend/mdescriptor_studio_backend/services/generation_service.py#L168-L202)。
- **现状**：提交时比较已有数据库中的 dataset fingerprint 与 descriptor metadata；`_seed_view()` 中有条件刷新数据集，但不使用 seed view 时未看到相同显式刷新；worker 只重新检查 dataset_id/status，未重新比对文件刷新 fingerprint。这是潜在 TOCTOU 风险（是否会被外围数据集服务自动刷新，需要集成测试确认）。metadata 缺少 fingerprint 时提交路径允许通过。
- **修复**：集中 `resolve_generation_reference()`，在提交与开始执行前刷新数据集、验证 dataset ID、fingerprint、行语义、全量帧映射；缺失 fingerprint 的旧记录要求重算或有显式兼容迁移。cache hit 也须先验证 source 不变。

### P1-03：局域环境目标与 FPS 空间不一致

- **源码**：[`engine.py` select_diverse_batch](https://github.com/nicheal/MDescriptorStudio/blob/e8797c810a53b5bb17486e7afcc4dff6da2a3a00/backend/mdescriptor_studio_backend/generation/engine.py#L143-L167)。
- **现状**：即使目标为 local_environment_novelty，评分使用原子描述符，但批内 FPS 固定用 mean-pooled 的结构描述符。两个结构可在均值上相近但包含完全不同的稀有原子环境，也可能反过来。这是策略目标错位，而非单纯 Python 错误。
- **修复建议**：保留现有结构 FPS 为明确基线，新建 `local_greedy_coverage`：选候选时评估其原子行相对 frozen + 当前批已选原子环境的**边际新增唯一数**，按边际增益选择，必要时用结构距离打破平局。不要用 PCA/UMAP 空间代替真实缩放描述符空间。
- **验收**：构造均值相同而局域环境不同的人工描述符样本，证明新选择保留更多真正不重复的原子环境；同预算对照，不以视觉散点云面积判定优劣。

### P1-04：GA 轮盘公式的注释和实现不一致

- **源码**：[`genetic.py` `_roulette_pick()`](https://github.com/nicheal/MDescriptorStudio/blob/e8797c810a53b5bb17486e7afcc4dff6da2a3a00/backend/mdescriptor_studio_backend/generation/optimizers/genetic.py#L216-L279)。
- **现状**：描述声称使用累计平方票数 `Σ_{k=1}^{m-r}k²`，实现实际使用 `(m-r)²` 作为单父代票数。两者选择压力不同。
- **修复**：先确认科学方法实际打算使用哪一种；将公式、代码、测试和论文叙述统一，若改变实现会影响随机轨迹，需升级算法版本并重做 Benchmarks。不可未经验证就改公式并沿用旧实验结论。

### P1-05：周期性自镜像接触及物理可行性判定待补强

- **源码**：[`geometry.py`](https://github.com/nicheal/MDescriptorStudio/blob/e8797c810a53b5bb17486e7afcc4dff6da2a3a00/backend/mdescriptor_studio_backend/generation/constraints/geometry.py#L33-L80)。
- **现状**：`_contact_violation` 对 `n<2` 直接跳过，而周期性 1 原子小晶胞也可能与自身周期镜像有过短接触；现有调用设置 `include_self_images=False`。默认 backend `min_distance_mode='none'`，GUI 默认却为 covalent。尚缺统一的物理筛选层次。
- **修复**：如适用，在全周期单原子及一般结构检查非零周期矢量形成的自镜像短接触；明确支持部分周期情况及邻居搜索图像范围。提供可显式关闭、默认适合目标材料的几何质量配置。更严格的能量/力/不确定性过滤须作为可选第二层，不要将几何有效宣传为热力学稳定。

### P1-06：发表级复现包缺失

- **源码/证据**：[`benchmark/README.md`](https://github.com/nicheal/MDescriptorStudio/blob/e8797c810a53b5bb17486e7afcc4dff6da2a3a00/benchmark/README.md) 说明 `benchmark/results/` 被 gitignore；当前提交 git tree 中该路径下有 `0` 个已跟踪结果文件。仓库有多份文字实验记录，但本次无法从仓库直接获得逐 seed 的原始输出。
- **修复**：提供匿名化/去本地路径的 `config.json`、`run_results.jsonl`、`summary.json`、`environment.lock`、图表生成脚本和 SHA256 校验。较大原始文件放 GitHub Release / Zenodo 等，仓库保留哈希和可访问清单。样本数据若无法公开，必须提供合成最小替代样本与真实数据取得说明。

### P2-01：请求整数与分支参数校验一致性

- [`models.py` `_int`](https://github.com/nicheal/MDescriptorStudio/blob/e8797c810a53b5bb17486e7afcc4dff6da2a3a00/backend/mdescriptor_studio_backend/generation/models.py#L183-L191) 把合法浮点数直接 `int()` 截断，`max_generations=3.9` 等不应静默接受；优化器数目已有 `_optional_count` 严格语义，可统一。
- PSO 构造函数已有 `pso_weight_anchor`，但 parser/catalog/UI 未暴露，目标 PSO 中无法从请求自定义该量；GA `gene_mutation_rate` 同样存在构造层与公共请求层差异。应明确内部实验旋钮与公开参数的边界。
- `state_dict()` 当前主要用于内存状态报告，工件没有完整优化器状态与 RNG state，取消后不可精确从断点继续；若未来需要 resume，独立定义状态版本和恢复接口，而不是宣传当前具有断点续算。

## 3. 详细实施路线：以验收门为核心

### Phase R0：冻结和留痕（优先级：立即；约 0.5—1 开发日）

**目标**：不混淆当前历史结果与新算法版本。

1. 新建修复分支，记录本审阅 commit、依赖 lock、操作系统、CPU、线程数、descriptor 类型与参数、数据集 fingerprint、数据集大小、NEP 模型版本；不要修改已有实验记录。
2. 为 R0 创建 `generation_verification_matrix.md`：区分 Python 单测、worker 端到端、Playwright mock E2E、真实 NEP 数据 Benchmark 和独立复现。
3. 在修复前保存当前 `golden baseline` 的运行语义；建立“指标版本、策略版本、Benchmark 版本”三套显式字段。

**验收门**：历史与新结果不会被自动混排；当前 CI 继续通过。

### Phase R1：P0 正确性热修（约 2—4 开发日）

1. 为 Random 提供统一的 operator selection/apply helper；消除定向参数捕获错误。
2. 重新实现严格逐环境 greedy union；统计 `raw_novel_environment_count` 和 `strict_unique_novel_environment_count`，并保留可追踪的定义。
3. 增加 10 个以上极小合成样例：重复行、非新颖阻断、跨结构重复、跨轮重复、边界阈值、不同 scaling、不同参考规模、多原子同均值、完全无新颖环境。
4. Benchmark 设置 `metric_anchors/search_anchors/region_radius` 三个不可混淆的变量；保证不同优化器的种子池构建与 production worker 使用同一实现。
5. 为 Benchmark 写无 NEP 依赖的小型 stub descriptor integration smoke：要求四类策略的锚点激活布尔值、强制锚点和半径判定逐项匹配。

**验收门**：每个缺陷在修复前应有红测、修复后绿测；Random 无锚点 golden baseline 保持不变（确实需要改变时明确版本升级）；`targeting_enabled` 的所有分支无歧义。

### Phase R2：科学指标与数据完整性（约 3—5 开发日）

1. 将完整 raw/strict_unique 定义贯穿 RoundRecord、convergence、artifact、preview、UI 和 discovery-rate stop。
2. `coverage_radius` 一律明确为“已接受生成集合对既有参考域的覆盖半径”；不能用原始归档自身半径（恒为零）混称。公开 `reference domain`, `distance scaling`，阻止不同描述符维度直接比较半径数值。
3. 同步修复 `generation.pca` 原始 novelty 计数的显示口径；在详情页区分 raw count 与严格唯一 count。
4. 在 submit、cache-hit 以及 worker 准备执行三个节点完成相同数据快照审查，异常返回结构化的 `ANALYSIS_STALE/RESULT_INCOMPATIBLE`。
5. 真实改写 extxyz 后立即启动旧描述符提交的竞态测试；验证不能命中过期生成缓存。

**验收门**：UI、artifact、停止条件、Benchmark 同一 run 的严格唯一数逐项一致；源数据修改后不会开始使用旧 descriptor 的任务。

### Phase R3：完成真正面向局域环境的 Maximin（约 4—7 开发日）

保留现有 `novelty ranking + structure-FPS` 作为 `selection_strategy=structure_fps_v1`，新增 `local_incremental_maximin_v1`；避免重构破坏历史 Random 基线。

1. 先选固定高适应度候选子池，随后按每个候选能引入的**边际新颖环境数**进行迭代接受，每接受一个候选，即更新当前批临时局域环境记忆，下一次重新计算边际增益。
2. 同时保留 `global coverage completion` 的独立结构级任务：可选严格 archive-warm-start FPS/边际覆盖半径下降，不能与局域环境发现混成同一性能指标。
3. 根据局域环境数量、原子数和描述符维度设计 chunked 距离、最大临时候选数和内存预算；性能测试比较当前方法与新方法在相同计算预算下的真实耗时与 peak RSS。
4. 定向搜索在 UI 提供两种明确的锚点语义：结构级目标（现有 mean-pooled 锚点）；局域环境目标（指定原子、物种过滤或选中局域环境）。局域目标必须在原子级描述符空间中定义，不能仅使用结构均值代表它。

**验收门**：在事先固定的合成基准上，新局域选择策略的严格唯一数不低于结构 FPS；真实材料数据上的结论依重复测量而非先验保证。

### Phase R4：公平重跑并重新决定 GA/PSO 地位（约 5—10 开发日，计算时间另计）

在**不再改 Benchmark 定义**后，执行预注册的配对实验。

1. 对照组定义：无定向 Random、无定向 GA、无定向 PSO、定向 Random、定向 GA、定向 PSO；若以“同父代资源”比较，则所有组使用相同强制锚点 seed_pool，只有定向组激活锚点搜索策略。
2. 主指标：每 100 次实际 descriptor evaluation 获得的严格唯一局域环境数；定向主指标：到目标环境的描述符距离分布/目标区域增益。次指标：accepted-only coverage、有效接受率、几何拒绝率、每种算子使用率、运行时间、内存峰值。
3. 最小基准：原有 carbon 数据集 20 paired seeds ×10,000 eval；增加另一类结构或多组分材料，防止算法只适合单一碳样本。再设留出数据集，调参禁止接触留出测试结果。
4. 固定同一数据、descriptor 参数、缩放拟合、预算与硬件线程设置。每个 seed 的全部参数/逐轮数据落盘；生成配对差值、bootstrap 95% CI、效应量与不同阈值敏感性。
5. 单独做针对目标搜索的消融：是否强制锚点、region_radius、移民份额、fitness 是否与目标距离对齐、GA parent-selection 公式、PSO anchor pull。研究假设在跑留出集之前确定。
6. **门控决策**：只有修复后的多种数据上有稳定实证改进，才考虑默认切换、GA crossover/NSGA-II 或进一步调 PSO。若 GA/PSO 仍未改善，保留可选研究模式，但 UI 不展示夸大性能的结论。

**验收门**：任一读者能由清单与数据重绘全部图表；生产 worker 和 Benchmark 的同参数、同 RNG 测试得到等价提案/指标。

### Phase R5：材料科学价值与产品化（约 1—2 周；可与 R4 的计算并行但不依赖未验证排名）

1. 将“几何合格”“描述符新颖”“能量/力筛选合格”“可进入训练集”建模为不同状态；不要直接把新颖结构等同于物理可信结构。
2. 物理约束：周期自镜像最短接触、原子数/化学计量、体积、最大累计形变、超出 descriptor cutoff 的局部异常；依据目标材料配置。增加若干随机小晶胞和单原子周期结构的 property-based 测试。
3. 在 UI 中增加真实运行前的资源估计、当前 active scientific metric、可重现参数摘要、错误和淘汰原因分布。展示严格 unique、raw 与接受率，明确 `region_radius` 的单位是对应 scaling 下的距离而非 Å。
4. 可选接入能量/力/模型不确定性二次过滤。当前项目以描述符分析为主；不要求直接训练势函数。若后续有训练设施，再设计独立 screening adapter 与批量缓存。
5. 运行持久化：当前可以 fixed seed 从头复现，但要实现真正 resume，还需记录 RNG bit-generator state、seed subset、优化器完整状态、归档版本及最后已原子提交的轮次。
6. 在版本化的 Benchmark 验收后，更新 GUI 中 GA/PSO 的实测提示文本和 README 科学能力陈述。

**验收门**：给出一个可信的小型材料案例，包含原始数据、候选生成、双层筛选、全程谱系、最终数据集和相同预算对比。

## 4. 推荐的首批 PR / Issue 切分

| 顺序 | PR 标题建议 | 必须附带的验证 |
|---|---|---|
| 1 | `fix(generation): bind selected operator params in targeted random` | 参数捕获红测/绿测、原 Random golden |
| 2 | `fix(generation): strict unique local environment counting` | 两个数学反例、批内/跨轮去重 |
| 3 | `fix(benchmark): separate scoring anchors from search anchors` | no-target 签名等价、所有 optimizer 共同 seed_pool |
| 4 | `fix(benchmark): use configured radius and worker-equivalent anchors` | 固定距离例的 within-radius、GA/PSO worker 装配等价 |
| 5 | `fix(generation): unify discovery metrics and freshness guards` | UI/artifact/stop 完全一致；并发数据修改测试 |
| 6 | `feat(generation): local-aware greedy selection v1` | 同均值不同局域环境的选择测试、性能上界 |
| 7 | `test(benchmark): publish versioned multi-dataset reproducibility pack` | 20-seed 原始结果、统计代码、依赖与数据指纹 |
| 8 | `feat(generation): chemistry-aware validation and target-local modes` | 物理短接触、物种过滤、局域锚点测试 |

## 5. 优先开发顺序与禁止事项

**现在开始：PR1—PR4 → PR5 → PR6 → PR7 → PR8。** PR1—PR4 完成前不要再投入较长 GPU/CPU Benchmark，不应把缺陷前的比较数值复制到论文的性能表。PR5 确定科学指标语义后再冻结新版实验合同。PR6 是否优于现有 FPS 须经新鲜且同预算实证；不要直接假定更复杂一定更好。

当前阶段不建议实施新的 optimizer family、SSW 对接或超参数大规模搜索。现存框架和测试基础已足够，瓶颈主要是**测度是否真实、试验是否公平、选择策略是否与研究目标同构**。这些问题解决之后，GA、PSO 或后续新搜索机制的研究价值才能被可靠量化。

## 6. 主要源码索引

- `generation/engine.py`：https://github.com/nicheal/MDescriptorStudio/blob/e8797c810a53b5bb17486e7afcc4dff6da2a3a00/backend/mdescriptor_studio_backend/generation/engine.py
- `generation/optimizers/random_search.py`：https://github.com/nicheal/MDescriptorStudio/blob/e8797c810a53b5bb17486e7afcc4dff6da2a3a00/backend/mdescriptor_studio_backend/generation/optimizers/random_search.py
- `generation/optimizers/genetic.py`：https://github.com/nicheal/MDescriptorStudio/blob/e8797c810a53b5bb17486e7afcc4dff6da2a3a00/backend/mdescriptor_studio_backend/generation/optimizers/genetic.py
- `generation/optimizers/pso.py`：https://github.com/nicheal/MDescriptorStudio/blob/e8797c810a53b5bb17486e7afcc4dff6da2a3a00/backend/mdescriptor_studio_backend/generation/optimizers/pso.py
- `generation/constraints/geometry.py`：https://github.com/nicheal/MDescriptorStudio/blob/e8797c810a53b5bb17486e7afcc4dff6da2a3a00/backend/mdescriptor_studio_backend/generation/constraints/geometry.py
- `services/generation_service.py`：https://github.com/nicheal/MDescriptorStudio/blob/e8797c810a53b5bb17486e7afcc4dff6da2a3a00/backend/mdescriptor_studio_backend/services/generation_service.py
- Benchmark：https://github.com/nicheal/MDescriptorStudio/blob/e8797c810a53b5bb17486e7afcc4dff6da2a3a00/benchmark/genetic_vs_random.py
- 现有计划：https://github.com/nicheal/MDescriptorStudio/blob/e8797c810a53b5bb17486e7afcc4dff6da2a3a00/docs/plan/06-plan-descriptor-guided-dataset-expansion.md
