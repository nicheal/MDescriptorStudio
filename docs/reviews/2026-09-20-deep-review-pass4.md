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

## 待修（已核实，按批排列）

### 第 4 批 · 热路径（结果须逐位不变，不 bump）

| # | 位置 | 问题 | 已有测量 |
| --- | --- | --- | --- |
| D-2 | `preview_service.py:67-70,123,131-138` | 每个点把 9 个 `_PREVIEW_ARRAY_KEYS` 各 `np.asarray` 两遍（`.ndim` + `len`）再逐个标量取；也是「生产者哪天给 list 而非 ndarray 就退化成二次方」的唯一入口 | 20k 点 × 9 数组：`_build_preview` 83 ms，其中光 `.ndim` 检查 19 ms。改为循环外转一次 + 列 `.tolist()` 后 zip |
| D-3 | `metrics/__init__.py:365-372`、`_common.py:661-672` | 第 11 批把邻居搜索搬出 Python 后，local diversity 还剩两个逐原子循环；无元素过滤时（`sample_indices = np.arange(n)`）CSR 重建 100 % 白做 | 20 000 原子 × ~60 邻居：226 ms → 向量化 12 ms；5 000 原子 81 → 4 ms |
| D-4 | `analysis_loader.py:200,216` | `_load_samples` 已拿到 `load_values` 解析好的 `row["metadata"]`，却又调 `_result_metadata(row)` 重开重解析同一文件（多一次 `validate_local_path` + 重走 rep 检查）。`result.heatmap`（Explore 原子表逐帧调）为了 `scope`/`frame_index` 两列付全额解析 | patch `Path.read_text` 计数：单次 PCA metadata.json 读 2 次，只需 1 次 |
| D-5 | `analysis_loader.py:156-175` ← `job_runner.py:59` | `_usable_run` 每个输入 run 验一次新鲜度，`_assert_dataset_current` 无缓存地走 `compute_fingerprint(use_cache=False)`（全目录遍历 + 32 MB 采样哈希 + 3 次 walk）。**一个**数据集上的多 run sensitivity/compare 付 N 遍，外加每 run 一次 `SELECT * FROM datasets`，全在 RPC 线程；`_input_ids` 对 `run_ids` 无长度上限 | 计数版：3 run 提交 → 数据集探针 3、`descriptor_runs` SELECT 6、metadata 解析 6。单次墙钟未测 |
| D-6 | `registry.ts:123-139` | 文件自己的注释把契约写成「每个视图真正读到的数组」，6 条违反它：`local_diversity` 取 `coords`/`sample_indices`/`labels`（`LocalView` 只用 `coordination` + `neighbor_distances`）；`mantel`/`kernel`/`perturbation_sensitivity` 各带一个没人读的 `sample_indices`；`sampling` 对所有非 FPS 算法取三条 FPS 覆盖曲线。`AnalysisResultVisualization` 用 `loading` 门住整个面板，所以每个没人读的名字都直接加在白屏时间里 | 现有门禁只查正向（列出的必须存在于 manifest），需补反向：表里的名字必须出现在该 kind 的渲染代码里 |
| D-7 | `trajectoryView.tsx:116-135,299-312` | 渲染体内每次 `setRange` 重算 8 组派生值，尤其 `box` 四次 `quantile()` 各复制并排序一遍可见区投影，而 antd `Slider` 每次 mouse-move 都触发；上游 memo 帮不上（工作在它们下游） | node 重放：2 万帧 `box` 13.5 ms + marker 1.9 ms；10 万帧 78 + 8.8 + 1.7 ≈ 90 ms，未含 Plotly。**与第 13 批否决的「投影面板点选 memo」是不同路径**，那条测的是 `selectedpoints` 的必要开销 |
| D-8 | `statistics.py` 的 `force_magnitudes` 累加 + 末尾 `np.concatenate` | 为一个精确中位数把全数据集每原子力幅值留在内存：峰值 8 B × 总原子数再乘一份 concatenate。该模块里唯一与它自己「streaming statistics」表头矛盾处 | 先确认有没有人依赖精确中位数；否则改表头说明这一处刻意全量驻留 |
| B-8 | `pairs.py:234-240` | Spearman Mantel 每置换重算 `rankdata`。对称矩阵同步行列置换保持非对角元素多重集，`rankdata(permuted) == rankdata(right)[inv]` 精确成立（含并列，已验 5 个随机置换 + 一个带并列的矩阵） | 600 样本/179 700 对 → 44.2 ms/置换（默认 999 = 44 s）；上限 2000/1 999 000 → 629 ms/置换 = **10.5 分钟**。同规模 Pearson 4.3/66.7 ms。秩只算一次约 10× |
| B-9 | `pairs.py:324,326` | `drift` 先调 `coverage(...)`（内部已跑 `_preprocess_reference_query`，进度到 1.0），再自己把同两个矩阵预处理一遍（第三、四份 float64 全量拷贝），然后做 MMD/协方差 —— 那段完全不碰 `progress`，而 `check_cancelled` 只在 `progress` 里触发（`job_runner.py:285`），于是进度钉在「完成」、主重阶段不可取消 | — |
| C-5 | `security.py:70-81`，调用点 `deepmd.py:79-86`、`:182-185` | `ensure_no_reparse_points` 对每个叶子把整条路径重走一遍，每段 6 类探针。`fingerprint._files:63,67` 用的就是「只查叶子」 | 1010 文件的树：**57 976 次探针 = 57.4/文件**，60 KB 数据 980 ms；端到端 4 042 文件 → 构造 3.7 s + `scan()` 3.8 s。**这是安全检查的覆盖面变更**：改之前必须先有真 junction 用例证明只查叶子仍能拒掉 |

### 第 5 批 · 语义变更，一次 `ANALYSIS_ALGORITHM_VERSION` bump（S2 已定调）

| # | 位置 | 问题 | 已有测量 |
| --- | --- | --- | --- |
| B-1 | `identity.ts` 第二半 + `restore.ts` + `Analysis.tsx:1292` | `c87f006` 只补了键；drift 面板是唯一没有 Granularity 控件的跨集面板，且 `restore` 对 drift 返回 `{}`，历史加载不回填粒度。于是别处动 `mode` 会让绿点静默消失，屏幕上无控件可解释或复原 —— 与第 13 批 outliers/`k` 同形，当时解法是给面板加控件 | — |
| B-3 | `correlation.py:19-22` vs `metrics/__init__.py:91,215` | 口径 2 的「零方差一个归属者」漏了两个判定点且方向相反：`feature_correlation` 判**方差** `> 1e-12`（绝对、平方单位 = std > 1e-6），`feature_variance` 判 **ptp** `<= 1e-12`。于是相关面板静默删掉所有距离/PCA/coverage/sampling 都在用的低幅值真特征、还报成 "zero-variance"（假）；方差面板留着 `_preprocess` 会删的大幅舍入噪声并计入 `effective_nonzero_dimensions` | 矩阵 `[N(0,1), N(0,10), N(0,100), ±1e-8, 1e6+几个 ulp]`：`_meaningful_scale` 留 0–3 删 4；`feature_correlation` 报 "ignored 2 zero-variance feature(s)" 且再无一对引用特征 3 |
| B-4 | `clustering/__init__.py:50-53,75-83` | Mahalanobis 无样本/秩守卫：`d ≥ n` 时 `np.cov`+`pinv` 把打分限制在样本张成空间，`+1e-10·I` 的脊被 pinv 直接截掉。Studio 常态 256 维 × 50–200 结构，永远在这个区间，面板照常报 `outlier_count` 不告警。`outlier()` 不调 `_check_samples`，n=1 走到全 NaN 协方差的 `pinv` → 未捕获 `LinAlgError`（knn/lof 正确回 `ANALYSIS_INSUFFICIENT_SAMPLES`） | n=50/d=60，一点沿别人不占的方向推到 500σ → 排名 **50/50（最后）**；同一路 n=400/d=20 → 排名 1/400。换 Ledoit–Wolf 属新增行为，本轮不做 |
| B-5 | `sampling/engine.py:175-191` vs `:96-102` | P1-14 第二半没落地、memo 也没定调：FPS 那支有 `scaling_mode`/`fit_scaling`/`apply_scaling`，cluster 直接 `.fit(x)` 原始值。混合单位矩阵（能量 eV + 维里 + 体积 Å³）上「代表样本」几乎完全沿最宽那一列选，`:201` 的 `_visual_pca(x)` 画的还是同一个原始空间，两张 sampling 卡不可比。`submission.ts` 又只在 fps 时发 `scaling`，屏幕上没人说明空间变了 | 两列 1:1000 的矩阵可复现选择由宽列主导 |
| B-6 | `pairs.py:142-148` + `analysisVisualizations.tsx:236` | `pick_scores` 不单调：每步拿当前 `np.ptp(min_diversity)` 重新 min–max，范围随批次收缩，第 k 步与 k+1 步不在同一把尺上，而面板按选择顺序画成柱状图。memo 断言它单调并指定了要钉的测试，落地的 `tests/test_analysis_engine.py:454-486` 只查长度与有限性。`:156-160` 的 `full_scores` 除以 `nanmax` 却不减 min，是第三个公式 | 40 seed × 3 组参数：120 次运行里 **118 次出现上升**，例 `[1.0, 0.7695, 0.8158, 0.7263, …]` |
| D-8 | `statistics.py` | 若改成流式（而非只改表头），随这批一起过 | — |

已随第 2 批落地的语义变更：A-2 pbc 拼写 + `FINGERPRINT_VERSION` v3→v4。

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
