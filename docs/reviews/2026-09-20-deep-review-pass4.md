# 深度审阅（第四轮）2026-09-20

范围：全仓。基线 `7d5605f`（第三轮第 15 批收尾）。本轮是**新一次审阅**，不是上一份报告的续批。
方法：5 个审阅 agent 分片（算法层 / 服务层 / 解析与协议 / 前端页面 / 状态与 mock），外加散装前端文件、`benchmark/`、`scripts/`、测试与 CI。每条候选结论经我重新读码或实测后才进入下面的清单。

环境提醒：跑测试必须用 `.venv/Scripts/python.exe`（mdescriptor 0.3.3 + hdbscan）。用系统 conda 的 python 会有 5 个失败，那是环境漂移（0.2.3、缺 hdbscan），不是代码问题。基线：pytest 343 passed / 1 skipped；vitest 195；Playwright 39；tsc / eslint / cargo 干净。

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

## 待修（已核实，按批排列）

### 第 4 批 · 热路径（结果须逐位不变，不 bump）—— 只剩 D-8

七条里六条进仓（上表 `ff3f69f`…`453253b`）。「逐位不变所以不 bump」这条线是守住了的：
Mantel 默认的 Pearson 分支**故意**保留从距离矩阵直接 gather —— 换成凝聚索引 gather 后
实测 0.6–0.8×（更慢），并在 1e-17 上改数，两头好处都没有。

一条热路径**故意不做**：`preview_service._build_preview` 末尾那句对整体重走的
`_json_safe` 约占该函数 80 % 运行时，换成「只净化算法自己那几个标量、信任循环构造」
能拿约 2×，代价是一条看不见的不变量；而 `preview_json` 现在以 `allow_nan=False`
落盘，漏网的 numpy 值会让作业当场失败而不是写坏行，所以那条全局遍历已不是唯一防线。
要做的话这是一个独立决定。

| # | 位置 | 问题 | 已有测量 |
| --- | --- | --- | --- |
| D-8 | `statistics.py` 的 `force_magnitudes` 累加 + 末尾 `np.concatenate` | 为一个精确中位数把全数据集每原子力幅值留在内存：峰值 8 B × 总原子数再乘一份 concatenate。该模块里唯一与它自己「streaming statistics」表头矛盾处 | 先确认有没有人依赖精确中位数；否则改表头说明这一处刻意全量驻留 |

### 第 5 批 · 语义变更，一次 `ANALYSIS_ALGORITHM_VERSION` bump（S2 已定调）

| # | 位置 | 问题 | 已有测量 |
| --- | --- | --- | --- |
| B-1 | `identity.ts` 第二半 + `restore.ts` + `Analysis.tsx:1292` | `c87f006` 只补了键；drift 面板是唯一没有 Granularity 控件的跨集面板，且 `restore` 对 drift 返回 `{}`，历史加载不回填粒度。于是别处动 `mode` 会让绿点静默消失，屏幕上无控件可解释或复原 —— 与第 13 批 outliers/`k` 同形，当时解法是给面板加控件 | — |
| B-5 | `sampling/engine.py:175-191` vs `:96-102` | P1-14 第二半没落地、memo 也没定调：FPS 那支有 `scaling_mode`/`fit_scaling`/`apply_scaling`，cluster 直接 `.fit(x)` 原始值。混合单位矩阵（能量 eV + 维里 + 体积 Å³）上「代表样本」几乎完全沿最宽那一列选，`:201` 的 `_visual_pca(x)` 画的还是同一个原始空间，两张 sampling 卡不可比。`submission.ts` 又只在 fps 时发 `scaling`，屏幕上没人说明空间变了 | 两列 1:1000 的矩阵可复现选择由宽列主导 |
| B-6 | `pairs.py:142-148` + `analysisVisualizations.tsx:236` | `pick_scores` 不单调：每步拿当前 `np.ptp(min_diversity)` 重新 min–max，范围随批次收缩，第 k 步与 k+1 步不在同一把尺上，而面板按选择顺序画成柱状图。memo 断言它单调并指定了要钉的测试，落地的 `tests/test_analysis_engine.py:454-486` 只查长度与有限性。`:156-160` 的 `full_scores` 除以 `nanmax` 却不减 min，是第三个公式 | 40 seed × 3 组参数：120 次运行里 **118 次出现上升**，例 `[1.0, 0.7695, 0.8158, 0.7263, …]` |
| D-8 | `statistics.py` | 若改成流式（而非只改表头），随这批一起过 | — |

已随第 2 批落地的语义变更：A-2 pbc 拼写 + `FINGERPRINT_VERSION` v3→v4。

### 第 4 批落地时对本报告的三处自我修正

1. **第 1 批的 A-1 修复自己引入了一次热路径回退，第 4 批量出来并修掉。** 把 `finite_or_none` 放进 `_json_safe` 后，净化函数对每个叶子多一次 Python 调用；cProfile 在 20 000 点 × 9 列上数到 420 000 次调用，约占该函数运行时 35 %。现在规则仍由 `datasets.statistics.finite_or_none` 表述（`frame_force_max`、`frame_energy_per_atom` 用它），但 `_json_safe` 里内联成一次 `math.isfinite`，注释指明是同一条规则。教训：给一个被遍历全树的函数加"每叶子一次调用"就是加常数开销，必须在加它的那批里量一次。
2. **`_load_samples` 的 metadata 二次解析（D-4）顺带让 `AnalysisPreviewMixin._result_metadata` 变成死代码**，已删除，并清掉它留下的三个 import。同一批还修掉第 1 批在 `artifact_service.py` 留下的一个**重复 import**（同一条 `from ..datasets.statistics import finite_or_none` 出现两次）——自查 diff 抓到的，不是 agent 报的。
3. **D-5 的「全在 RPC 线程」是错的。** `job_runner._run_analysis` 跑在作业自己的线程上，不在 RPC 线程；探针次数（3 → 1）与代价（冷 26.3 ms / 热 3.3 ms）都成立，线程归属不成立，落地时按实测写法纠正。同一条 finding 里的「`_input_ids` 对 `run_ids` 无长度上限」是真的，但它是个契约问题而不是探针问题，留在第 6 批。

### 第 6 批 · 契约与工具诚实

| # | 位置 | 问题 |
| --- | --- | --- |
| A-7 | `main.rs:164-177,225-264` + `App.tsx:75-88,250` | `backend_restart` 可重入：`spawn_backend` 先花 ~1 s（release）哈希 ~470 MB bundle 才 `command.spawn()`，然后覆写 `state.child`；两次重叠提交产生两个孩子，输的那个 `Child` 被赋值 drop（Windows 只关句柄不杀进程），`kill_backend` 再也够不到 → 两个 sidecar 打同一个 SQLite 与同一个 webview 通道，第一个 reader 线程收不到 EOF 永不退出。前端 `restartingRef` 只压 toast，`restartBackend` 提交前不测它，Restart 按钮无 `disabled`。修法：`spawn_backend` 整体持锁 + 在飞行时拒绝 + 按钮禁用 + 一个 Rust 测试断言两次重叠提交只剩一个孩子 |
| B-7 | `metrics/__init__.py:561-587`、`sensitivity.py:106-112`、`:433-446` | 口径 1 的「把生效尺度写回结果」漏了 `trajectory`/`sensitivity`/`local_diversity`：它们发布 `median_step_distance`、`event_threshold`、`median_neighbor_distance` 这类随尺度变的量却不记 `_preprocess` 用的是哪个，历史行说不出 `2.4` 是 σ 单位还是描述符单位。`coverage`/`overlap`/`acquisition`/`effective_dimension` 都记了。纯加法，不需要失效 |
| C-6 | `preview.tsx:1383-1387` | mock 校验 setting 的 key 然后把值扔掉，`settings.get` 对 6 个 key 里 5 个回字面值/`null` → `hydrateAnalysisUi`（view + slots）、`hydrateActiveRun`、`initLanguage` 在浏览器测试里永远走「什么都没持久化」分支；reload 半边（含 `v:2` 与旧 slot 判别）零 e2e 覆盖，写的那半边是绿的。`test_backend_response_contract.py:28-30` 还专门论证过要抓 `settings.get` |
| C-7 | `preview.tsx:405-416` | `mockRecordAnalysisRow` 硬编码单 `descriptor_run_id`、默认单 inputRun/单 dataset，而 `analysis.drift`/`sensitivity`/`compare`/`mantel` 不覆盖默认 —— 尽管 `ANALYSIS_RUN_PARAMS` 正在校验它们带两个 run。于是 e2e 看到的每条 `analysis.list` 都是生产不会产出的形状，依赖它的两个前端判据只吃过退化输入 |
| C-8 | `preview.tsx:52,101,218,272` vs `statistics.py:578` | mock 广告真后端永远报不出的能力：`energy.per_atom: true`，而 `statistics.py` 硬编码 `"per_atom": False`、无任何读者产出逐原子能量属性。`Overview.tsx:265` 正好渲染它 → 真 sidecar 那列永远 `—`，mock 每次预览都 ✓。**乐观方向的错误证据** |
| C-9 | `preview.tsx` 的 `stats` 无 `stats_version`；`protocol.ts:75-78` 声明必在 | 类型注释断言「到得了 UI 的 payload 一定带它」，mock 回的 `stats` 却没这个键，也缺 `health_findings.nonphysical_distances`。`STATS: Record<string, unknown>` 故意无类型所以 `tsc` 看不见，金标契约只记 3 键信封。同一测试文件已镜像另外三个兄弟常量。更省的做法：把 mock 的 `STATS` 标成 `Record<string, Stats>` 让 `tsc` 去数剩下的洞 |
| C-10 | `errors.py` vs `protocol/server.py:189,223`、`mdescriptor_adapter.py:121,144` | `BUSY` 只作为字面量存在（`errors.py` 里没定义），而 `stores/jobs.ts:162` 在它上面分支；第 5 批门禁按 `{vars(errors) 里的大写常量}` 算「后端会发的码集」，字面量对它隐形 → mock 无法被绑到 `BUSY`，拼错的码也能上线并退化成 "Request failed." |
| C-11 | `export_service.py:141,150,156` vs `:181,204-208` | 存下来的 `selected_count` 是调用方原始列表长度，而写文件用 `sorted(set(...))`、按范围过滤、结构级写者还按唯一帧折叠 → 一条记录能说「选了 500 个」而文件里 312 行/180 结构。同文件 `_write_sampling_report` 已经是对的 |
| C-12 | `job_runner.py:128` vs `:195` | 同一个 `preprocessing_json` 两个写点且已漂移：QUEUED 行写 `{preprocess, scaling}`，落定改写成 `{preprocess}` —— 对复合 FPS，`scaling` 是真定义采样空间的参数 |
| C-13 | `analysis_service.py:125-127` + `analysis_loader.py:118,151-153` | `_group_labels_cache` 注释写「tiny LRU」，实现是不重排 + `pop(next(iter())))` 的 FIFO（刚跑完的作业要的那条可能正是被丢的），且声明的 key 类型（3 元组）与实际（4 元组）不符 |
| C-15 | `jobs.ts:23-50` + `JobsDrawer.tsx:70` | ui-review 与 state-review 各自独立报出同一处：`JOB_TYPE_PAIRS` 是 `registry.ts` 那张表的第二份手抄，漏了 UI 自己会提交的 `analysis.mantel`、`analysis.perturbation_sensitivity`、`dataset.view.materialize`（直接打印方法名），第四条是**错的**而非缺失：`analysis.acquisition` 固定标 "Novelty acquisition"，而同一 method 也可能带 `acquisition_method: "uncertainty_diversity"` —— 这个区分提交侧已知，是 `trackJob(jobId, method)` 扔掉了 |
| C-16 | `benchmark/run_benchmarks.py:187-206` | `storage` 的 read 行报 `mb_per_second`，实测 **4 452 / 4 476 MB/s**，write 只有 265–300 MB/s —— 同一文件连读三次测的是页缓存不是存储带宽。README 的措辞勉强算诚实，但一张 MB/s 表会被论文当 I/O 数字引用（要投 CPC/JOSS 的那份）。在行里和 README 标明 cache-warm 即可；绕开缓存要 Windows admin 权限，不成比例 |
| C-17 | `job_runner.py:_input_ids` | 对 `run_ids` 无长度上限：一次提交带 N 个 id 就有 N 次 `SELECT * FROM descriptor_runs`、N 次 `feature_space_signature`，且 `input_ids` 会被拼进缓存身份。第 4 批把每 id 一次的数据集探针收成每次提交一次之后，剩下的按 N 线性项都在这里 —— 是个契约问题（要不要设上限、上限是多少、超了报什么码），不是性能问题 |
| E-2 | `analysis_service.py:249-251` + `Analysis.tsx:469,478,483` | `truncated` 只在 `ndim == 2` 时才可能为真，而加载器除 trajectory/effective_dimension 外取一块就 `break`。Mantel/Compare 于是画「全域均匀抽样的前 40 %」—— 有偏子集被当作关联强度，旁边 "Pairs" 写完整数量；property 面板在原子模式常年只覆盖 2 万样本而 KPI 写 "Samples"；同面板散点是跨全域 stride 的，两者描述的不是同一批样本。**S3 定调：如实标注** —— 后端对一维也报 `truncated`，前端把行不足并进 `narrowedArrays` 语义并显示「前 2 万 / N」 |
| E-5 | `Explore.tsx:384-389` + `exploreFrameLoader.ts:45` | 外部跳转被在飞的取帧吞掉：健康抽屉点一行设 `activeFrameIndex` 并切到 Explore，内部请求还在飞时 effect 因 `loading` bail，而它提交时 `onFrame` 里 `setActiveFrame(idx)` 把外部跳转覆写回它本来在取的那帧 —— 抽屉说「预览第 100 帧」，屏幕停在第 6 帧且无报错。loader 的代际守卫是对的，缺的是重放被跳过的那次导航 |
| E-7 | `preview_service.py:146-147` + `featureVariance.tsx:225` | 后端 `result["warnings"]` 只在 `analysis_type == "feature_variance"` 时抄进 preview，也只有这一个面板渲染它 → `_preprocess` 丢掉的非有限/零方差列、"pairwise matrix limited to 400 deterministic samples"、trajectory 的 MAD 回落告警、local diversity 的邻居表溢出提示全到不了界面；`DataTable` 还把 `warnings` 显式过滤掉。**注意这条会动响应形状**（preview 多一个键），需重生成金标 keys 并单独说明 |
| E-8 | `RightRail.tsx:147-197` + `Explore.tsx:55-70` + `Overview.tsx:32-65` + `HealthFindingsDrawer.tsx:76-90` | 「取统计 → 等作业 → 再取一次」四份实现且已漂移：只有 RightRail/HealthFindingsDrawer 等完调 `refetchDatasets()`，所以 Explore/Overview 重算后数据集那一行（`dataset_service.py:635` 重写 `number_of_frames`/`fingerprint`/`last_scan_at`）还是旧的；`loadExploreHealth` 已抽出并单测但另外三处没收进去。次要：rail 的 `rescan()` 自己请求一次后又 `bumpStatsTick()` 触发自己的 mount effect → 多一次往返 + 一帧七行健康项全空 |
| Q5 | `preview.tsx` 的 METHODS 表 | 7 个前端真发的 RPC 没有 handler（`descriptor.submit`、`job.cancel`、`analysis.delete`、`dataset.remove`、`dataset.rename`、`dataset.view.materialize`、`analysis.fps_quota`），拿到的是 `NO_HANDLER` —— 真后端永远不会发的码。于是 e2e 建不出描述符 run、也走不到任何拒绝/失败分支。**定调：补 `descriptor.submit` 与 `dataset.remove`，其余五个在门禁里显式列例外并写明理由** |
| S4 | `datasets/base.py:56-66` + `datasets/readers/` | `metadata()`/`iterate_frames()`/`read()` 这组「插件词汇」在 `services/` 里没有生产者，只有自证式测试在调，`read()` 还会把 25 万帧一次性 list 化。第 4 批以完全相同理由删过 `DescriptorMatrix.granularity`。**已定调删除** |
| D4' | `dataset_view_service.py:295,333`、`export_service.py:134`、`security.py:145-152` | 导出/物化目标路径在 RPC 线程查「存在就拒」，几分钟后由作业以 `O_CREAT\|O_WRONLY\|O_TRUNC`（无 `O_EXCL`）写：用户在此期间自建的同名文件被静默截断；两次同时提交同一路径能穿过检查互相插；`materialize` 完全没去重。**定调：`O_EXCL` 占位 + runner 复查，不新增 overwrite 参数** |

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
