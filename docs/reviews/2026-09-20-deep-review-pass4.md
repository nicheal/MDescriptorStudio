# 深度审阅（第四轮）2026-09-20

范围：全仓。基线 `7d5605f`（第三轮第 15 批收尾）。本轮是**新一次审阅**，不是上一份报告的续批。
方法：5 个审阅 agent 分片（算法层 / 服务层 / 解析与协议 / 前端页面 / 状态与 mock），外加散装前端文件、`benchmark/`、`scripts/`、测试与 CI。每条候选结论经我重新读码或实测后才进入下面的清单。

环境提醒：跑测试必须用 `.venv/Scripts/python.exe`（mdescriptor 0.3.3 + hdbscan）。用系统 conda 的 python 会有 5 个失败，那是环境漂移（0.2.3、缺 hdbscan），不是代码问题。开轮基线：pytest 343 passed / 1 skipped；vitest 195；Playwright 39；tsc / eslint / cargo 干净。全部批次收尾后的门禁：pytest 378 passed / 1 skipped；vitest 203；Playwright 44；tsc / eslint 干净；`cargo test` 4 passed（最后一次 Rust 改动是 `8cb3a38`，其后的提交没有再碰 Rust）。

## 已落地

| 提交 | 主题 | 验证 |
| --- | --- | --- |
| `c87f006` | drift 的身份键补上 `mode`；不再发两个没人读的参数；新增 `identity.test.ts` 扫「请求变了键就必须变」 | vitest 195，反向验证：改回那行会重新点名 `mode` |
| `707e470` | 数据可信性：非有限值永久毒化已存结果、`descriptor.submit` 不验产物、`result.list` 缺 tie-break、pairwise 最小值恒为 0、mock 词表门禁看不见 `dataset.view.*`、近乎常量的列让 `feature_variance` 崩 | pytest 348（+5），三条门禁反向验证 |
| `4bfa042` | 解析器：pbc 拼写（随 `FINGERPRINT_VERSION` v3→v4）、UTF-8 BOM、DeepMD 尺寸门重复计数、Python 数字宽容漏进线上格式、`get_frame` 物种二次遍历 | pytest 352（+4），四条逐个撤改动→测试红→复原 |
| `5b312f4` | 前端诚实：trajectory 阈值少抄后端守卫、结构预览造样本下标、恢复的失败作业码当消息、三条英文界面无文本、`describeError` 最后一处手抄 | vitest 198（+3）、Playwright 39 |
| `ff3f69f` | D-2 预览点构造里重复的 `np.asarray` 摘出（实测只 5 %，不是报告的 23 %）；D-4 `metadata.json` 每次加载只读一遍，顺带让 `_result_metadata` 成死代码并删掉 | pytest 353，两处各自撤改动→测试红 |
| `d072c4a` | D-3 local diversity 剩下两个逐原子循环之一：CSR 邻居表搬出 Python，且无元素过滤时不再重建 | 20 000 原子 226 ms → 12 ms |
| `b986a1e` | D-6 `ARTIFACT_ARRAYS` 收到「视图真读的数组」，并补上反向门禁 | 一次打开少 1.2 MB JSON 与六次往返 |
| `dd247b9` | B-8 Mantel 的 Spearman 秩只算一次；B-9 drift 不再把同两个矩阵预处理两遍，且 MMD 阶段终于可取消 | 上限 2 000 样本 629.6 s → 54.8 s（9–19×），Pearson 逐位不变；drift 进度检查点 1 → 4，输出逐位不变 |
| `a361576` | D-5 一次提交只探一次数据集新鲜度（探针集合归调用方所有，不跨请求缓存） | 3 个探针 → 1；单次探针冷 26.3 ms / 热 3.3 ms |
| `a82b352` | D-7 trajectory 可见窗口统计进 memo，四次排序变两次 | node 重放 10 万帧 102.9 ms → 50.5 ms，输出逐字段相同 |
| `453253b` | C-5 一次列目录共享已验证前缀；并为 `is_reparse_point` 建第一批真 junction 用例 | 探针 106 → 12、`scan()` 2.1 ms → 1.1 ms；把检查改成「只看叶子」会让三条新测试全红 |
| `0aeb2c4` | B-3 「这个特征有没有信息」只剩一个归属者：`feature_correlation` 与 `feature_variance` 都改判 `_meaningful_scale`，`variance_threshold` 默认从 1e-12（平方单位）归零 | 报告那组四列矩阵：旧规则删掉 4 列里的 3 列并让面板直接报 "at least two non-constant features are required"；两条规则各自恢复后测试逐名变红 |
| `6ba8823` | B-4 Mahalanobis 报出它实际张量到的维度，并且 n=1 时结构化拒绝（原先 `pinv` 抛 `LinAlgError` 把整个作业打死） | 打分表达式逐字未变，363 通过、无一条既有期望被改动；`LinAlgError: SVD did not converge` 是对 `_preprocess` 留下的那个矩阵实测出来的 |
| `d84ead3` | B-6 acquisition 的多样性项改在循环前定尺，`pick_scores` 因此单调不增，`scores` 与末次 pick 逐值相等 | 40 seed × 3 组参数：120 次运行里 100 次上升（报告原 fixture 118 次）→ 0 次；把 running-ptp 那行放回去，测试直接把上升的 trace 打在消息里 |
| `546187e` | `ANALYSIS_ALGORITHM_VERSION` → "studio-analysis-6"（B-3/B-4/B-6 三处改数），mock 由词表门禁同步 | pytest 363 / vitest 198；两个站点少改一个，门禁当场点名 |
| `0905ff1` | B-1：drift 面板补上 Granularity 控件：drift 面板补上 Granularity 控件，`restore` 终于回答 drift 行 | 新 Playwright 用例（第一次碰 Granularity）；删掉那一行控件它就红 |
| `7498086` | S4 删掉 `metadata()/read()/iterate_frames()` 插件词汇三件套（`services/` 里零生产者，`read()` 会把 25 万帧 list 化） | pytest 363；两个自证式测试改成测真契约 |
| `bab2fd9` | C-10 `BUSY` 成为 `errors.py` 声明的码，公共消息进 `_PUBLIC_MESSAGES` 一处 | 消息逐字不变；词表门禁从此数得到它 |
| `918ebbd` | C-12 `preprocessing_json` 两个写点收敛成 `_preprocessing(params)`，落定不再丢 `scaling` | 撤回旧写法测试点名 `scaling` |
| `baafdec` | C-11 导出记录「写了多少条」而不是「被给了多少个下标」：`_write_export` 返回 `(path, written)` | `[2,2,7,7,7]` 的 indices 导出写 2 行、记 2 |
| `f5916a2` | C-13 `_group_labels_cache` 成为真 LRU，key 类型不再谎报三元组 | 命中后再塞 1 条即证：撤掉 touch，被刚用过的键先被丢 |
| `eec90c8` | C-6：mock 的 settings 存下来了（localStorage），重启页面终于能测「读回来」那一半 | 新 Playwright 用例选模块→等写入→reload→断言模块还在；把 `settings.get` 改回字面答复它就红 |
| `4b94052` | C-7：mock 的分析行带上真实输入（两条 run、两个数据集），`descriptor_run_id` 取首条 | 新用例先 Swap 把配对移开、再从历史载入，要求 query 行回到 Si；drift 退回单输入行即红 |
| `f93f2b9` | B-7：trajectory / local_diversity / sensitivity 把生效尺度写进 preview（`_preprocess_mode` 与 `_preprocess` 读同一个键），面板用现成的「特征尺度」芯片显示 | 12×4 实测三条默认 `standardized`、显式 `raw` 时如实报 `raw`；撤掉 mock 那行 Playwright 即红 |
| `77a999f` | C-8：mock 不再广告 `energy.per_atom`（后端把它写死成 False，`Overview` 照它渲染 ✓）；新门禁从 `statistics.py` 读「被写死关闭的能力」并禁止 mock 声称 | 放回 `true` 即点名 `['energy.per_atom']`；`fixed_off == {energy.per_atom}` 一条保证门禁不会静默变空 |
| `b1c88b0` | C-9：mock 的 `STATS` 改标 `Record<string, Stats>`，两条 payload 补上 `stats_version: 5`，顺带拆掉一个不成立的 cast | `tsc` 自己数出两个缺字段；新门禁绑 `STATS_VERSION`，改成 4 报 `{'4'} != {'5'}` |
| `3f00938` | C-15：抽屉里三个被打印成裸 RPC 名的作业补标签；`analysis.acquisition` 不再替两种目标断言「新颖性」 | vitest 22→24；两条新断言分别是「标签不得等于方法名」与「不得声称做不到的事」 |
| `be22297` | D4'：物化目标从「先查后写」改成「先占后写」—— 独占创建 + 落盘前复查占位是否还空 | 去掉 `O_EXCL` 与复查，新测试报 `DID NOT RAISE`；导出（export）**故意不套**这条：覆写同一目标本就是它的文档行为，而定调又排除了 `overwrite` 参数 |
| `0360986` | C-16：benchmark 的 storage read 行标清「页缓存」，README 单列一句别当 I/O 带宽引用 | 本机实测 write 3 723 / 4 311 MB/s、read 5 120 / 5 461 MB/s；`--suite storage --quick` 跑通，行里带 `cache` 字段 |
| `4032af7` | E-2：`analysis.chunk` 对任何秩都报「这是数组的一部分」，面板把它并进 narrowed 提示并写出「20 000 / 179 700」 | 既有 cache+chunk 测试钉住 4/12 为 truncated、整页为 false；mock 原来恒报 false，一并改对，于是这条提示在浏览器里第一次可达 |
| `f393bef` | E-7：后端把 `warnings_json` 读给所有分析（原先只有 feature_variance），渲染移到结果卡片一处 | 新用例聚类一个空特征列并问 preview；撤掉读路径即 `KeyError: 'warnings'` |
| `2db2e62` | E-5：在飞取帧期间到达的外部跳转被记住并在落地后重放 | 规则抽成 `resolveExternalFrame` 并单测四态；旧的「loading 就丢」在第二条断言上失败 |
| `373d896` | Q5：mock 补 `descriptor.submit`（含缓存命中与 force）与 `dataset.remove`，其余五个作为具名例外进门禁 | 新 wire-contract 用例走真 RPC：缓存答复、强算、完成后新增 COMPLETED run、未知数据集拒绝、删除后列表里没有它 |
| `8cb3a38` | A-7：shell 只有一个后端 spawn 名额，重启不再留下杀不掉的 sidecar；前端提交前自测在飞标记 | `cargo test` 4 通过（新断言：第二次 claim 必须失败、guard drop 后重开）；去掉 `compare_exchange` 该测试在断言处 panic |
| `0933533` | E-8（陈旧那一半）：`loadExploreHealth` 增加 `onRecalculated`，Explore 传 `refetchDatasets`，Overview 就地补同一步 | Overview 用例断言顺序 `statistics → dataset.list → statistics`；Explore 用例数出钩子发生在第二次读之前；任一处去掉调用即红 |
| `7c8a837` | C-17：一次提交的逐 run 校验合成一条 `IN` 查询（定调：不设长度上限，去掉线性代价而不是发明拒绝） | 三 run 提交数出恰好一次带三个占位符的读；改回逐 id 循环它报三次；缺失 id 仍点名自己 |
| `65702c9` | D-8：力幅值在 8 MiB 预算内仍全量保留、逐位不变，超过预算才改流入式计数网格；`STATS_VERSION` 5 → 6 | 11 组差分在保留路径上逐位相等；网格路径边界/min/max/mean 精确、计数最多差 6/970、中位数差半格；480 万值上 115.2 MB/88 ms → 8.6 MB/181 ms；注入「永不切网格」内存断言在 51.4 MB 处红 |
| `3fb25fc` | B-5：cluster 代表样本改在 FPS 那套尺度空间里选，控件、身份键、恢复与结果卡一起说明用的是哪个空间；`ANALYSIS_ALGORITHM_VERSION` → "studio-analysis-7" | 三列 1:1000 实测 raw 从 `[1,10,22]` 漂到 `[3,15,22]`、两种尺度模式对两份单位都回 `[1,17,22]`；删掉身份键里的 cluster 分支，扫描门禁报 `ignores ["samplingScaling"]` |

## 待修（已核实，按批排列）—— 本轮清单已清空

下面三批的每一条要么是**已落地**（见上表），要么在原地标明了**故意不做**及其理由；另有被实测否决的见「反证记录」，需要你裁决而不是修错的见「明确留给你、不动的三件」。

### 第 4 批 · 热路径（结果须逐位不变，不 bump）—— 已关闭

七条全部进仓（六条见上表 `ff3f69f`…`453253b`，D-8 见 `65702c9`，它不在这批的「不 bump」额度里）。「逐位不变所以不 bump」这条线是守住了的：
Mantel 默认的 Pearson 分支**故意**保留从距离矩阵直接 gather —— 换成凝聚索引 gather 后
实测 0.6–0.8×（更慢），并在 1e-17 上改数，两头好处都没有。

一条热路径**故意不做**：`preview_service._build_preview` 末尾那句对整体重走的
`_json_safe` 约占该函数 80 % 运行时，换成「只净化算法自己那几个标量、信任循环构造」
能拿约 2×，代价是一条看不见的不变量；而 `preview_json` 现在以 `allow_nan=False`
落盘，漏网的 numpy 值会让作业当场失败而不是写坏行，所以那条全局遍历已不是唯一防线。
要做的话这是一个独立决定。

D-8（`statistics.py` 的 `force_magnitudes` 累加 + 末尾 `np.concatenate`）由 `65702c9` 落地，走的不是报告给的那两条路（确认有没有人依赖精确中位数 / 只改表头）里的任何一条，而是第三条：1M 个原子以内仍然全量保留、仍然由 `_hist`/`_summary` 出数，超过预算才改流入式计数网格。因此它不在「逐位不变」这一批的额度里 —— 见下面自我修正第 5 条。

### 第 5 批 · 语义变更 —— 已关闭（两次失效都花掉了）

原定「整批一次 bump」，实际提前收尾：B-3 / B-4 / B-6 已经改了缓存结果所描述的
样本，让它们继续被旧结果命中不是选项。所以下面每条落地时**各自**判断是否改数、要不要再一次 bump。B-1 由 `0905ff1` 按「给面板加控件」落地，不改后端数字；B-5 由 `3fb25fc` 落地，改采样结果，于是你在本轮授权了第二次 bump（`ANALYSIS_ALGORITHM_VERSION` → "studio-analysis-7"）；D-8 由 `65702c9` 落地，但改的是数据集统计而不是分析结果 —— 让它真正失效的是 `STATS_VERSION` 5 → 6，分析版本对它无意义。两个修订号都动了，代价如实记下：每个数据集下次打开重扫一遍统计，每条分析结果下次提交重算一遍。

两条都已落地：B-5 见 `3fb25fc`（cluster 与 FPS 共用尺度空间，`raw` 仍是恒等、仍可复现旧选择，默认值改数所以带 bump；面板的 Feature scaling 控件、身份键、历史恢复与结果芯片同时跟上，`random`/`stratified`/`per_element` 不声称自己没用过的尺度）；D-8 见 `65702c9`（预算内逐位不变，预算外流式计数）。

已随第 2 批落地的语义变更：A-2 pbc 拼写 + `FINGERPRINT_VERSION` v3→v4。

### 落地时对本报告的自我修正

1. **第 1 批的 A-1 修复自己引入了一次热路径回退，第 4 批量出来并修掉。** 把 `finite_or_none` 放进 `_json_safe` 后，净化函数对每个叶子多一次 Python 调用；cProfile 在 20 000 点 × 9 列上数到 420 000 次调用，约占该函数运行时 35 %。现在规则仍由 `datasets.statistics.finite_or_none` 表述（`frame_force_max`、`frame_energy_per_atom` 用它），但 `_json_safe` 里内联成一次 `math.isfinite`，注释指明是同一条规则。教训：给一个被遍历全树的函数加"每叶子一次调用"就是加常数开销，必须在加它的那批里量一次。
2. **`_load_samples` 的 metadata 二次解析（D-4）顺带让 `AnalysisPreviewMixin._result_metadata` 变成死代码**，已删除，并清掉它留下的三个 import。同一批还修掉第 1 批在 `artifact_service.py` 留下的一个**重复 import**（同一条 `from ..datasets.statistics import finite_or_none` 出现两次）——自查 diff 抓到的，不是 agent 报的。
3. **D-5 的「全在 RPC 线程」是错的。** `job_runner._run_analysis` 跑在作业自己的线程上，不在 RPC 线程；探针次数（3 → 1）与代价（冷 26.3 ms / 热 3.3 ms）都成立，线程归属不成立，落地时按实测写法纠正。同一条 finding 里的「`_input_ids` 对 `run_ids` 无长度上限」是真的，但它是个契约问题而不是探针问题，留在第 6 批。

4. **C-9 的「mock 也缺 `health_findings.nonphysical_distances`」这条不成立为待办。** 后端确实发这个键，但 `HealthFindings` 类型里没有它、前端也没人读它 —— `Explore` 的最小距离对是从已经拿到的帧现算的（`minimumDistancePair(frame.xyz, ...)`）。把它补进类型只会加一个没有读者的字段；真要它得先有界面用途。落地时按这个结论只做了一半（见 `b1c88b0` 末尾说明）。

5. **D-8 的失效不属于第 5 批那份授权的字面对象，而属于另一个修订号。** 报告把 D-8 放在第 4 批（热路径、逐位不变、不 bump），又在第 5 批附一行「若改成流式，随这批一起过」；真落地时它是流式的，改的却是 `dataset_statistics` 里存着的数，而那道门是 `dataset_service._cached_stats` 比对的 `STATS_VERSION`（`analysis_helpers.ANALYSIS_ALGORITHM_VERSION` 对它完全无效）。所以本轮把你授权的「一次 bump」花成了两处：`STATS_VERSION` 6（D-8）与 "studio-analysis-7"（B-5）。记账理由写在这里而不是藏在 diff 里：只动分析版本号的那次提交会带着永远无法失效的旧统计发布出去。

### 第 6 批 · 契约与工具诚实 —— 已关闭

| # | 位置 | 问题 |
| --- | --- | --- |
| C-17 | `job_runner.py:_input_ids` | **已定调并落地**（`7c8a837`）：不设上限 —— 渲染器发不出的提交不需要新错误码，上限只是给「要得更多」的用户发明一种失败。剩下的按 N 线性项收成一条 `IN` 查询（`_usable_runs`），逐条裁决（缺失点名、STALE 拒绝、无结果 `RESULT_INCOMPATIBLE`）原样保留 |
| E-8 | `RightRail.tsx:147-197` + `HealthFindingsDrawer.tsx:76-90` | **剩下的重复按你的决定留着**：这两处仍各自实现「取统计 → 等作业 → 再取一次」，并且都各自驱动一条进度条，收进 `loadExploreHealth` 属界面改动而非修错。 陈迹那一半已由 `0933533` 解决：helper 多了 `onRecalculated`，Explore/Overview 重算后会刷新数据集行，两处断言合起来钉住 `statistics → dataset.list → statistics` 的顺序。 |

## 反证记录（不要再报）

- **「`umap_numpy.py:197-203` 的斥力不是 UMAP 的梯度，导致远距簇塌缩」——不成立。** 对照本仓库自带的 umap-learn 源码 `src-tauri/resources/backend/_internal/umap/layouts.py:167-182`：`grad_coeff = 2γb; grad_coeff /= (0.001 + dist_squared) * (a * dist_squared**b + 1); grad_d = clip(grad_coeff * (current[d] - other[d])); current[d] += grad_d * alpha`，与仓库实现**逐字相同**；吸引力那条（`umap_numpy.py:182` ↔ `layouts.py:137-143`） likewise。真实存在的是两件事：(1) 批式近似的抽样制度差异（每 epoch 均匀抽边 × 固定负例数，而非 umap 按 `epochs_per_negative_sample` 的度数驱动时刻表；正例权重在 clip 之后才乘）——实现选择，需受控对比才能判断代价；(2) 唯一的质量门禁 `tests/test_umap_numpy.py::test_separable_blobs_preserve_local_structure` 只要求 `trustworthiness > 0.9` 且 blob 间距 σ=4，**看不见簇塌缩**。该修的是门禁，不是公式。
- **「`novelty_fps` 也读 `uncertainty_k`，所以 `c87f006` 删错了」——不成立。** `pairs.py:86-87` 的 `if acquisition_method in ("uncertainty", "uncertainty_diversity", "knn_uncertainty"):` 在读取之前，novelty 分支走 `else` 且从不取名 `uncertainty_k`。删掉是错的删除，不是错的行为。
- **「Mahalanobis 在 n=50/d=60 时，把一点沿别人不占的方向推到 500σ 会排名 50/50（最后）」——没复现。** 那个点本身把该方向写进了协方差（秩从 8 变 9），标准化后它仍是分数最高、仍是唯一被打上 outlier 的点。B-4 成立的部分是弱的那一半：50 个样本在 60 维上至多张成 49 个方向，面板照常报 `outlier_count` 而不说这件事 —— 已按实测落地（`6ba8823`，测试里连「仍被标出」一起钉住）。n=1 走 `pinv(NaN)` 抛 `LinAlgError` 是真的，两行脚本实测出来。
- **「`dataset_view_service.py:295` 调了一个不存在的 `_export_destination`」——不成立。** 它定义在 `:286`，同一个类里。
- `types.ts:85-88` 与 `:132-135` 重复声明 4 个字段：TS 允许且无害，改它只产噪声 diff。
- `analysisChartKit.tsx` 的 `overviewLayout`/`layout`：8 与 55 处调用，是两种视图的默认值，不是重复。
- `trajectoryMath.ts:31-35` 与 `analysisChartKit.ts:140-148` 的分位数同式两写：前者文件头明写「不依赖 React/Plotly 以便直接单测」，合并就要把它拖进带 Plotly 的模块。
- `elements.ts` 的 `ATOMIC_MASS`：有 `util/structure.ts` 在读，不是死代码。
- `ghosts.py:68` 的展开式：抵消误差 ~2.2e-16·r²，10⁶ Å 内的结构翻不动 2.4 Å 判定，`np.maximum(...,0)` 已兜底 —— 不可达输入。
- 混合周期 `[T,T,F]` 仍按 ADR-28 拍平（`test_mixed_periodicity_still_flattens_to_periodic` 钉着）。A-2 谈的是**拼写解析**，与它不是一件事。
- `exporters.write_deepmd` 的跨帧物种不一致、extxyz 索引与帧读的列上限不一致：第三轮第 95/96 行已登记为 latent，本轮复核仍在原位。
- 投影面板点选 memo：第 13 批已实测否决；D-7 是 trajectory 滑杆这条**不同**路径且有新测量。
- `stats` 里的 `energy.per_atom`、`Stats` 四个 legacy 字段、`preview_service` 的 points/rows、`JobsDrawer` 的 `mergeJobRows`、`Explore` 的第二次 `parseViewerAtoms`：见第三轮记录，均已处理或已否决。
- 两处 nit 不计数：`Sidebar.tsx` 加数据集输入框 `readOnly` 却带一个永不触发的 `onChange`；`DescriptorResults.tsx` 的 `descriptorColumnWidth` 对 `tableLayout="fixed"` 无上界增长。

## 明确留给你、不动的三件

1. `preview.tsx`（1821 行）实际是 4 个文件：fixture 数据 / analysis 预览 / 方法表 / Tauri stub。拆的收益是可维护性而非正确性，且它已被三个门禁钉住。
2. `scripts/verify_native_geometry.py`：实测能跑（400 随机帧 + 8 边界用例全一致，worst |Δ| = 1.3e-14），但没有任何东西跑它，仓库里只剩旧审阅文档提到它；而 `tests/test_native_stats.py` 只有 9 个固定用例 —— 能抓出「歪 cell」那类缺陷的随机对拍躺在 CI 外面。
3. `scripts/create_analysis_report_pdf.py`：246 行、不 import 后端、`reportlab` 两处 requirements 都没声明、输出目录被 gitignore、被引用数 0；它产的那份中文《描述符分析与物理解释指南》PDF 在仓库里（400 KB，未跟踪）。
