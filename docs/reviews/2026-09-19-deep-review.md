# 深度代码审阅报告（HEAD 90b0a11，2026-09-19）

范围：后端 Python 12.6k 行 + C++ 原生核 675 行、前端 TS/TSX 18k 行、Rust 桥 618 行、pytest 7.4k 行、e2e 761 行、脚本与 CI。
方式：9 个只读审阅切片逐行通读 + 2 个复核切片对候选缺陷逐条判定 + 主线程亲自复算关键项。

核验标记：`✅` 我逐行读过代码或实测；`◑` 复核代理实测（脚本/数值验证）；`○` 单审阅者读码，我未独立复算。

---

## 一、Bug：会产生错误结果或坏行为

### P0-1 `analysis.export` 丢掉 Scope（view_id），会把错的帧写盘 ✅
`Analysis.tsx:1073` 的 payload 只有 `{run_id, indices, mode, format, output_path}`，同页所有计算都带 view_id（`submission.ts:93`、`Analysis.tsx:1213-1223`）。后端 `export_service.py:188` 用 `_load_samples(run, {"mode": mode}, "export")` 解析 sample→frame，而 `analysis_loader.py:272-274` 证明**只有传了 view_id 才切片**。选区里的 index 是"视图切片后"的位置，回到未切片空间就是另一批帧；越界 index 还被 `export_service.py:190` 静默丢弃。用户看到"导出成功"，拿到的是同号但不同的结构。
修复：export payload 带上 `view_id`，`_write_export` 透传并校验 run 与 view 一致。

### P0-2 打开历史记录时整表加载描述符矩阵，然后返回空数组 ✅
`artifact_service.py:227` 只收集 7 个数组名，`feature_correlation`/`effective_dimension`/`kernel`/`pairwise`/`compare`/`sensitivity`/`export` 提交的数组名都不在其中 → `arrays == {}` → `:244` 仍执行 `_load_samples(...)`（`analysis_loader.py:201` 的 `np.asarray(..., float64)` 是全量物化），而 `_build_preview`（`preview_service.py:52`）整个函数以 `"coords" in arrays` 为门 → 恒空 → `:245` 落 `[]`。前端每次点历史行都会走这条路（`Analysis.tsx:536`）。`analysis_service.py:177-182` 已为 `feature_variance` 写过同样理由的注释，却只补了一个类型。
修复：`if not arrays: return []`（置于 `:244` 之前）。

### P0-3 warm-start FPS 绕过视图校验与缓存身份 ✅
`job_runner.py:60` 是 `if cross_dataset or warm_start_fps:`，`:`84 是 `elif params.get("view_id")` → 对 warm-start FPS 该分支**永不执行**；但 runner 在 `:133-139` 确实按 `params["view_id"]` 切候选集。三个后果：视图内容哈希不进缓存键（`_canonical_params` 保留 view_id 却不含内容哈希）→ 编辑已存视图后仍命中旧结果；stale/跨数据集视图不在入队时拒绝，与 `:110-112` 自述"先校验再入队"矛盾；`:88-89` 的注释声称在此限定视图，实际从不执行。
（一个复核代理判定"已被注入"，我复核 `:60/:84` 的 if/elif 结构后维持原判。）
修复：把 view_id 校验 + 取 hash 提成独立 `if`，scope 只由一处构造。

### P0-4 原子级 run 缺 `row_offsets.npy` 时，把每个原子行伪装成一个结构 ✅
`analysis_loader.py:239-258`：`mode="structure"` 只有 `valid_offsets and declared_atom` 才走 `_pool_rows`，否则 `frames = arange(values.shape[0])`、`sample_ids = "frame:{i}"`。原子级 run（`:224` 判定为真）一旦 offsets 缺失/行数不符，就逐原子贴上"帧号"→ 前端反查跳错结构、drift/trajectory 的距离序列与 color-by 全部失真，且**无任何告警**；同一段数据在 `mode="atom"` 下是硬报错的（`:226-229`），严格性不对称。生产端早已把判据写进产物：`descriptor_service.py:628` 的 `row_offsets_verified` 全仓零读者。
修复：`else` 分支要求 `not declared_atom`，否则 `AppError(RESULT_INCOMPATIBLE)`；并让 `row_offsets_verified` 成为该判据来源。

### P1-5 "Color by" 是一个死控件 ✅
`analysisPreview.ts:33-42` 的 `normalizePoints` 逐字段复制，**不复制** `energy_per_atom/force_max/volume`（而 `AnalysisPoint:24-26` 声明它们存在）；`Analysis.tsx:1021` 正是读这三个字段 → `hasColor` 恒 false → 散点永远单色 `#0F6CBD`、无 colorbar、无任何提示。后端确实给了数据（`analysis_loader.py:62,75-79`，`job_runner.py:159-166` 注释写明 "for color-by"）。根因是前端有两条 points 正常化路径：`analysisShared.tsx:144-155` 的 `pcaPayloadPoints` **保留**这些字段，而活着的这条不保留。
修复：`normalizePoints` 补三行透传；非 PCA 或无有限值时隐藏该 Select。

### P1-6 失败数组被永久缓存成空数组 ✅
`Analysis.tsx:478-480` `catch { return [name, []] }`，`:481-490` 无条件写入模块级 `analysisCache`；`:446` 用 `hasOwnProperty` 判定"已取到"。于是一次偶发失败（作业取消、产物临时锁定）让相关性面板从此显示 "Correlation matrix is unavailable."，切走再回**不重试**，只能重启应用。`analysisCache.test.ts` 只测 LRU 条数/字节。
修复：失败名不写 key（区分"缺"与"空"），或记 `failedArrays` 并在重新选中该 analysis 时重试一次。

### P1-7 DeepMD reader 不过滤非有限值，帧级读取变成协议错误 ✅
`deepmd.py:228,231,234,237` 对 energy/forces/virial/coords 没有有限性检查（只有 box 在 `:212` 被检查），而 `extxyz.py` 有 `_finite()` 且注释写明理由就是"non-finite 无法编码进渲染器能接受的帧"。一帧含 NaN 力 → `frames.encode`（`allow_nan=False`）失败 → `Server._encode` 兜成 INTERNAL_ERROR；而 `statistics.py:430-440` 的 `NaN > 阈值` 恒为 False，这类帧不会被任何健康检查标记。`tests/test_dataset_health.py:110` 只钉住"统计不再崩"，读取侧无覆盖。
修复：数组型 reader 复用 `_finite` 语义（非有限 → 拒绝该帧），或在 `frame()`/`findings()` 出口统一 None 化。

### P1-8 `runningJobs` 只由事件驱动，错过一次心跳就永久错乱 ✅
`stores/jobs.ts:245-252` 是唯一重算点，只挂在 `job.progress`/`job.finished`；`trackJob:92-111`、`reconcileTrackedJob:199-204` 改了 `jobs` 却不重算。后端重启后靠 `job.get` 轮询补成终态的作业（`jobs.test.ts:61-71` 明确钉住"finished 事件被漏掉"）不再产生任何事件 → `StatusBar.tsx:30-33` 与 `JobsDrawer.tsx:40` 的角标永久残留。
（复核否证了"纯 QUEUED 不计数"：`:247/:251` 的 filter 含 QUEUED，只是延迟到下一次事件。）
修复：抽 `recountRunning()` 三处统一调用；或删掉这份跨 store 反范式化副本，由 `useJobs` 派生。

### P1-9 3Dmol 异步创建与卸载竞态，每次泄漏一个 WebGL 上下文 ✅
`Explore.tsx:387-406`：`await createStructureViewer()` 返回后只有 `if (cancelled) return;`，而 cleanup 已用仍是 null 的 `viewerRef.current` 调过 dispose → 刚建好的 viewer 从未 `clear()`/从未 `WEBGL_lose_context`。`StructurePreview.tsx:36-54` 同形。dev（`AppMount.tsx:24` StrictMode）每次进 Explore 必漏一个，prod 在 `import("3dmol")` 完成前切走也漏；后果正是 `viz/StructureViewer.ts:63-70` 注释警告的"约 16 个上下文后最早的静默变白"。
修复（一行，不动 viz 边界）：`if (cancelled) { disposeStructureViewer(element, viewer); return; }`。

### P1-10 表单显示值 ≠ 提交值，并连带污染缓存键 ◑
`SchemaForm.tsx:298-308` 的 `collectDefaults` 只播种 `meta.required && meta.default !== undefined`，而控件 `:126/:136/:147/:155/:177` 对缺省一律显示 `schema.default`。后端 `_validate_parameters`（`descriptor_service.py:336-354`）不回填默认，`:245` 又用**原始** parameters 算 `cache_key` → 用户把可选项显式改回默认值 = 产生一个与"从未碰过"不同键的 run，缓存白白失效。
修复：`collectDefaults` 对所有带 `default` 的非 model 参数播种（或在后端合并默认值后再算键）。

### P1-11 无关刷新会清空用户在描述符页编辑的参数 ✅
`Descriptors.tsx:154-158` 的重置 effect 依赖 `elementOptions` 的数组身份；`refetchDatasets()` 整体替换 `datasets` → 元素数组身份变化 → `setValues(collectDefaults(...))` 抹掉编辑。触发不需要用户额外动作（`RightRail.tsx:155-165` 在统计需补算时自动 refetch，与 Descriptors 同屏；另有 `RightRail:184`、`HealthFindingsDrawer:82,90`、`Sidebar:136`）。
修复：把依赖换成内容键 `elementOptions.join(",")`。

### P1-12 `settings.set` 即发即忘 + 4 线程池 → 同 key 写入乱序落库 ○
`persistence.ts:14,24` 与 `workspace.ts:80-88` 都是 `void ipc.request(...)`；`server.py:41` `max_workers=4`，`_write_lock` 只串行化输出不串行化写库，`database.py:231-236` 是无条件 UPSERT（last commit wins）。按住步进器或连点两个模块 → 后发先至，重启后阈值/活动 tab 回滚。
修复：`persistence.ts` 内按 key 串行化 + 约 200 ms 去抖。

### P1-13 灵敏度事件阈值在 ≥50% 同值步长上退化成 median ○
`_common.py:247-256` 的 `robust_sigma = 1.4826*mad` 没有 mad==0 退化保护（消费方 `metrics/__init__.py:512-513`）。轨迹每帧记两次 / 描述符被量化 / MC 拒绝帧 → median=0、MAD=0、threshold=0 → `event_rate≈0.496`，且每条事件的 `threshold_ratio` 因 `event_threshold>0` 不成立而全为 `None`，UI 只剩一堆没有倍率的"事件"。
修复：`robust_sigma` 非正时回退 `mean + sensitivity*std`，或对 threshold 加相对下限。

### P1-14 同一面板、同一输入给出相反结论：preprocess 默认值不一致 ○
`pairs.py:14` coverage 默认 `"raw"`，`:33,78` overlap/acquisition 默认 `"standardized"`；`submission.ts:96-110` 不发送 preprocess，preview 也不回写（`pairs.py:29,51-62`）→ `job_runner.py:190` 存成 `{"preprocess": null}`，缓存结果无法回溯单位。实测：query 的有信息列整体 ×1000 → coverage 报 100% 覆盖，overlap 对同一对输入报 100% 全新。同层已有正确先例：`metrics/__init__.py:440-444` 把默认写回 params。另 `sampling/engine.py:171-181` cluster 在原始 x 上跑 KMeans，而 FPS 分支 `:98-101` 走 `fit_scaling`。

### P1-15 零方差判据是绝对 eps，把纯舍入噪声当满权重信号轴 ○
`_common.py:132` `keep = scale > np.finfo(float64).eps`，随后 `:148` 除以该尺度。某列在 1000.0 上只有几个 ulp 抖动（std≈2.4e-13）→ 被保留并标准化成 std=1.0，于是每个距离、PCA、UMAP、coverage 阈值都掺进与真实描述符等权重的噪声维（D_real=30 + 噪声 20 时约 40% 平方距离是噪声）。同一列在 `correlation.py:20-21`（方差>1e-12）、`metrics/__init__.py:91,215`（ptp≤1e-12）、`sampling/preprocessing.py:60-62`（eps）得到三种判决 → 面板之间对"有哪些特征"直接互相矛盾。
修复：改成相对量级判据 `scale > tol*max(|mean|,1)`，四处共用一个 helper。

### P1-16 配位数被显示预算静默削顶 ○
`_common.py:585-587` 先 `[:max_neighbors]` 再 `coordination[global_index] = len(ordered)`。实测 3000 原子、cutoff 3 Å、`max_neighbors=64` → mean==max==64、`neighbor_count` 恰等于 `3000*64`。密排金属在 6 Å 下配位可达 80–120，默认 128 也会封顶——一个物理解读量不应受 artifact 上限支配。
修复：`coordination = len(contacts)`，截断只作用于 CSR 行；preview 加 `coordination_truncated_count`。

### P1-17 acquisition 上报的 scores 不是驱动选择的那个目标 ○
`pairs.py:137-141` 用逐步更新的 `normalized_diversity` 决策，`:148-154` 循环结束后用最终 `min_diversity` 重算 `full_scores`（池外点一律 0.0）。实测选 12 个：`[0.65,0.618,0.551,0.462,0.598,…]`，第 12 名分数高于第 6–10 名；用户按 scores 复核"为什么选它"会得到与 `selected_indices` 相反的结论。

### P1-18 FPS 与邻居搜索用两套互相矛盾的平方距离算法 ○
`fps.py:202-218,270` 用展开式 `|a|²+|b|²−2ab`，而 `_common.py:388-392` 明确注释拒绝该恒等式（cancellation），`tests/test_umap_numpy.py:79-93` 还把这条不变量钉成了测试。实测（n=400,d=96，公共偏移 1e6、散布 1e-3，float64）：`argmax` 选中真值 1.8e-4 而非最大 3.0e-4，中位相对误差 80%，400 行排名全翻转。默认 `robust` 缩放会掩盖它，但 `scaling:"raw"` 是 UI 选项（`restore.ts:98`）。

### P1-19 `_aligned_space_metrics` 仍用"第 0 列即自身"排除自己——同文件已修好并有测试的那类 bug ✅
`_common.py:633-634` `argsort(...)[:, 1:k+1]`，而 `:181-189` 的注释与 `tests/test_analysis_engine.py:625-652` 已确立"必须按身份排除自身"。300×8 含 100 个精确重复行 → 73/300 行把自己列成邻居，真实邻居被挤掉一个；`neighbor_overlap` 位置式 0.9287 vs 身份安全式 0.9527，直接显示在 compare/sensitivity 面板。
修复：复用 `_nearest_distances(a, k, "euclidean")`。

### P2 及其他（读码确认，影响较小或需产品口径）
- `job_runner.py:450-455` strain 把 cell 绕原点缩放、把 positions 绕质心缩放，中心不一致 → 扰动帧除应变外混入一次平移；tests 只跑 jitter（`test_analysis_ipc.py:140`），无测试钉住。需确认应变扰动的方法学口径。
- `server.py:65-73`：`_encode` 的字节长度测量在 `try` **之外**，`UnicodeEncodeError` 会击穿"永不沉默"保证（孤立代理对场景）。实测 SQLite 拒写此类字符串，故 RPC 响应路径由 `_handle:220` 顶层 except 兜住；真正的残余风险是 `emit()` 从作业线程抛出时丢掉 `job.finished`。建议把测量挪进 try 并一并 catch。
- `App.tsx:109-133` `handleReady` 无代际校验：旧进程的握手可在 `setBackendError` 之后继续跑到 `:127` 的 `setBackendReady`，用 `connected===false` 的通道渲染整套 shell（每个请求同步 `BACKEND_DOWN`），直到新进程再发 ready 才自愈。`st` 在 `await` 前捕获、`await` 后使用（`:85` vs `:96`）同类。
- `security.py:96` 的 `expanduser()` 是死代码（◑ 实测 `~/x` 早在 `:44` 被拒），`:97` 的 `is_absolute()` 又重复 `:44`。
- `restore.ts:119-121` + `Analysis.tsx:764`：载入一条"局部环境"历史行会把 `mode:"atom"` 写进**持久化**的 `view.mode`（该字段被 6 个模块共用），回到描述符空间后粒度已悄悄变了，重启后还会从 settings 恢复。删掉这条恢复映射里的一行即可。
- `result_service.py:38-46` `sweep_abandoned` 是唯一不经 `validate_managed_path`/id 正则就 `rmtree` 的写路径（`security.py:3-6` 明确把持久化 SQLite 行列为不可信输入）；`descriptor_service.py:594` 的 `run_id.removeprefix('run_')` 是字面恒等式。
- `dataset_view_service.py:232-241` 三次 `_insert` 各自提交（`remove` 却用了事务）：第 2/3 个失败会残留 `.../Train`，重试被 `:212-216` 判"名字已存在"，用户只能手删视图。`dataset_service.py:290-334` 同型（血缘可静默丢失）。
- `extxyz.py:276` 索引期与 `:119` 读帧期的列数上限不一致 → 文件能扫出帧数却每帧读取失败。`base.py:96-107` 把空数据集判成 mixed 周期性。`base.py:71-74` 的 `startswith("set")` 与 `deepmd.py:99` 的 `glob("set.*")` 判据不同 → 探测说 deepmd、适配器说"no frames"。
- `exporters.py:106-111` `type.raw` 只按帧 0 写、却用全帧元素集，且只校验原子数恒定不校验类型顺序（当前两个调用方都喂同构 DeepMD 帧，故未爆），与模块头"written system is guaranteed to reload"矛盾。

---

## 二、冗余代码 / 死代码

| 位置 | 情况 |
| --- | --- |
| `result.heatmap` | 全仓**无前端调用者**（只有 `main.py:153` 方法表 + `test_analysis_flow.py:96`）。功能要么没接上要么已被取代。 ◑ |
| `descriptor_service.py:628` `row_offsets_verified` | 写入 metadata，**零读者**（含前端/tests）。它本该是 P0-4 的判据。 ◑ |
| `preview_service.py:52-92` | 同一次采样用**同一个** `indices` 写两遍（`points` 与 `rows`，只有键名不同：`label/cluster/element` vs `labels/cluster_labels/element`）→ 每份预览传 2 万条记录两遍，逼近 8 MB 帧上限。注意：记忆里"points 用 linspace、rows 取前 20k 覆盖不同子集"的旧残留**已被修好**（`:75-78` 注释即为此），今天的问题是重复而非不一致。 ✅ |
| `main.py:63-79` vs `:210-221` | 同一份 6 字段版本载荷手写两份（`backend.ready` 与 `system.info` 从此可漂移）。 |
| `_NOW` 四份 | `job_service.py:22`、`analysis_helpers.py:15`、`dataset_service.py:49`、`descriptor_service.py:42`（`job_runner.py:24` 已在 import 其中一份）。`_ARTIFACT_ID_RE` 两份；托管路径校验两套（`_managed_artifact_path` / `_managed_result_path`+`_managed_analysis_path`）。 |
| "cross 样本取哪一侧" 三份词汇表 | `job_runner.py:58` 四类（coverage/overlap/acquisition/drift）、`artifact_service.py:239` 两类、`analysis_loader.py:87` 第三份。重叠分析的重建行因此以 reference 身份配 query 数组。 |
| `registry.py:157-179` | 别名（`iforest`/`isolation-forest`/`isolation_forest`/`mahalanobis_distance`/`hierarchical`/`agglomerative`/`element`/`per_element`…）各自成为 **RPC 方法 + 缓存身份** → 同一计算换拼写就重跑、再落一份产物、再进一次历史。`sampling/engine.py:66` 还接受从未注册的 `farthest_point`（不可达分支）。 |
| `models.py:167-169,197-199` `granularity` | 正是本文件 docstring 宣称"用类型取代的运行时字符串分支"，生产代码零读者，只有自证测试；`AnalysisAlgorithm` Protocol 只作文档（`register` 走鸭子类型）。 |
| `_ANALYSIS_SCHEMA_VERSION` | 恒为 1、只写不读（`is_complete` 不比较），与 preview 里的 `schema_version`、以及真正生效的 `feature_*_schema` 参数键**同名三义**。 |
| `analysis_artifact_store.py:158-173` | `metadata["preview"] = preview` 与 DB `preview_json` 同源同内容（最多 20k 点），把每次提交的序列化与磁盘占用翻倍，无读者。 |
| `security.py:96`、`config.py:43` | 死 `expanduser()`；`cache/` 目录创建后全仓（Python/Rust/前端/tests）零使用者。 ◑ |
| `deepmd.py:22` 注释 | 声称 `_SYMBOL_TO_Z`/`_Z_TO_SYMBOL` 是 re-export，全仓无人从该模块导入它们。 |
| Epic-001 拆分残留 | `dataset_view_service.py:27` import 宿主的私有 `_NOW,_frame_indices` 并调 `datasets._meta/_row/_adapter_for`；`dataset_frame_service.py:14` import 私有 `_symbol, formula_of`；`:99-102` 自己重算 force max，绕开专为"两处视图不要各算各的"而提取的 `statistics.frame_force_max`，`:98` 与 `dataset_service.py:565` 的 energy_per_atom 精度还分别 6/5 位。 |
| 前端重复实现 | 数字格式化三份（`analysisChartKit.tsx:117 fmt` ≡ `featureVariance.tsx:520` ≡ `analysisEffectiveDimension.tsx:205`）；`medianOf` ≡ `quantile(v,0.5)`；`MetricStrip` ≡ `Metrics`；`overviewLayout` 与 `layout` 只差两个常量；结果表两份规则不同（`analysisShared.tsx:189-206` 前 7 列/toPrecision(6) vs `analysisVisualizations.tsx:644-648` 前 8 列/过滤 parameters）；投影散点装配两份；粒度 Select 八份（`analysisShared.tsx:328` 的选项标签已漂移成 `t("Atom")`）；`MISSING_PROP_LABELS`（`Explore.tsx:37`）与 `util/properties.ts:6-10` 逐字相同——后者的文件注释恰好在警告"第二份副本会漏翻译新属性"。 ◑ |
| `Explore.tsx:37`、`stores/workspace.ts:109-118` | `activeDataset` 全仓只被自己的 docstring 提及；`refetchDatasets`(`workspace.ts:144-151`) 与 `refreshDatasets`(`App.tsx:81-102`) 同一件事两份实现、行为已分叉（只有 App 版会在无 active 时按持久值补选并落盘），而 5 个调用点走的都是不会自愈的那份。 ◑ |
| 脚本 | `verify_native_geometry.py` 仓内无任何调用者（400 帧差分测试实际没人跑）；`create_analysis_report_pdf.py` 未入库且 `:228` 指向已不存在的 `analysis/engine.py`；`package.ps1` 整条复刻 release.yml 的发布路径并硬编码仓库 URL；`probe_engine.py` 与 `verify_known_issues.py` 各自重探同一批引擎事实。 ◑ |

---

## 三、结构问题（屎山风险）

1. **注释与实现不符已成风**，且比无注释更危险：`dataset_service.py:50-52`"the drawer pages through longer lists"（实为固定 `limit:1000` 单页，`total` 还被 `HEALTH_FINDINGS_CAP=5000` 污染，`dataset.findings` 根本没有 offset → 第 1001 条之后永久不可达，`HealthFindingsDrawer.tsx:281-282` 把截断值显示成 "of N frames"）；`result_service.py:336`"never stream the full matrix over IPC"（只限列 ≤256、不限行）；`analysis_service.py:210-233` 的"row-bounded and value-bounded"（只限行/列、不限总值数）；`job_runner.py:88-89`（见 P0-3）。
2. **前后端两份算法目录且无 parity 测试**：`features/analysis/registry.ts` 与 `analysis/registry.py` 已不一致（`novelty_fps`/`uncertainty_diversity` 其实是 `acquisition_method` 取值而非分析类型）；后端新增名字只会静默变成历史条目的空导航。数组契约同理：`registry.ts:118-133` 声明的数组名与实际消费者严重脱节（见下节效率）。
3. **类型在说谎**：`types/protocol.ts:8` 声明 `details?`（后端故意不发）、`error_id` 全仓**零消费者**（后端 `_handle:215` 专门按它记日志）→ 实际呈现是 12 处手抄 `${code}: ${message}`（`Analysis.tsx:337,647,867,1084,1119`、`Descriptors.tsx:260`、`DescriptorResults.tsx:68,97` 等），用户手上没有任何可回报标识。`Stats` 的 `compositions?/health?/health_findings?` 可选性已被后端 `required` 判据（`dataset_service.py:481-493`）变成永不可能 → 由此产生死防御（`Overview.tsx:322,414`、`Explore.tsx:205`）和一条无防环的补救分支（`HealthFindingsDrawer.tsx:86-91`）。
4. **同一条不变式多个 owner 且方向相反**：`nearZero ≤ lowVariation` 在 `analysisUiStore.ts:47-56` 是"抬高对方"、在 `Analysis.tsx:1303` 是"压回输入"、在 `navigation.ts:42` 与 `restore.ts:141` 各钳一次 → store 的抬升分支从 UI 路径**不可达**，只有直接调 store 的测试能触发（`stores/analysisUi.test.ts:254-259`）。`preprocess` 有四种口径：`navigation.ts:52` 是该函数里唯一不做白名单的枚举字段，`restore.ts:50` 一律回落 `"center"`（umap/tsne 实算 raw → 谎报），`navigation.ts:17` 默认又 `"raw"`。
5. **参数状态双份存储的具体泄漏**：`outliers` 的 `paramsKey` 含 `k`（`identity.ts:25`）、提交也用 `k`（`submission.ts:61`），但该面板根本没有 `k` 控件（`Analysis.tsx:1242` 只有算法/contamination/granularity）→ 在别的页改 k 后回来，绿点全部消失、Run 实际用新 k 算，而页面上没有任何控件能把 k 调回去。
6. **模块边界靠下划线私有名维持**：见冗余表最后一行；`_meta` 的重指纹代价正是这样扩散出去的。
7. `Analysis.tsx:19` 从 `features/analysis` barrel 取 `hydrateAnalysisUi`，把 registry/submission/restore 一并钉进启动 chunk，与 `:25-26` 的分块意图相反。分析 UI store 实现在 `features/analysis/`、测试在 `stores/`。

---

## 四、低效率

1. **同一请求内把整份内容指纹算 2–3 遍**：`descriptor_service.py:217-219` → `dataset_service.py:668`（`use_cache=False`）、`:242` 同参再算、作业里 `:610-614` 第三遍；`dataset_service._meta:132-136` 在 legacy 分支算出的 `current` 两个分支都不使用；`dataset_view_service.py:68` 让 `list()` 与 `split()` 按视图行数放大 N+1。每次指纹是"整目录遍历 + 最多 32 MB 采样"（`fingerprint.py:146-154`）。前端已被迫绕开：`HealthFindingsDrawer.tsx:126-127` 注释明说切 tab 不能再问。 ◑
2. **scipy 回退路径在大帧上必然 OOM**：`statistics.py:167-169` 一次性物化 `S×n×3`（去重后 S≤98）→ 实测量化 1e6 原子约 3.9 GB、1e7 约 25 GB+，而预检允许 1e7（`deepmd.py:25,38,66`），统计扫描本身无原子数闸门；C++ 核是逐 shift 流式（`mds_native.cpp:561-571`）。只在原生核缺失或 `MDS_DISABLE_NATIVE` 时触发——与下面第五条叠加成真风险。另 `:353` 对同一帧做两遍 `_prepared_points` + 两次 cKDTree。 ◑
3. **邻居搜索又慢又不可取消**：`_common.py:570-589` 每原子 `query_ball_point` 后对每个候选单独 `np.linalg.norm`（cKDTree 排序时早算过），且整个函数没有 progress/取消点（取消只在 `job_runner.py:280` 的 progress 回调里生效，`metrics/__init__.py:339` 之前没有任何回调）。实测 20k 原子/60 Å 胞/6 Å cutoff：**104 s**、tracemalloc 峰值 294 MB，10 万原子约 8–10 分钟无法中断。`fps.py:426-433` 分组路径也没把 progress 传给每组 `farthest_point_sampling`（签名 `:84` 支持）。 ○
4. **`_analysis_row` 用 `SELECT *`**：`artifact_service.py:153` 在 `get/delete/preview/chunk` 每个热点请求里把可达数十 MB 的 `preview_json` 读出来再 `_json_load` 丢掉，而 `analysis_service.py:44-49` 的注释恰好说明了列表为什么要避开这个 blob；`chunk` 被前端按数组分页循环调用（`Analysis.tsx:462-476`）。 ◑（同类：`dataset_service.py:559-562` 每次打开抽屉为最多 1000 帧重做整趟最近邻，而统计遍历时已算过 `min_distance`，只在 `:509` 留了直方图。）
5. **导出进度全程停在 0%**：`export_service.py:22-26` 的 `_cancellable_frames` 只 `check_cancelled()`、从不 `ctx.progress`，runner 仅 `:119` 报 0 / `:141` 报 1；"每 250 帧上报 + 取消"在 `dataset_service.py:267-274`、`:615-625`、`dataset_view_service.py:282-290` 各写一遍且写法略有差异——`dataset_view_service.py:283-284` 的注释还点名要"像 export_service 一样延迟取帧"，却只对齐了延迟、没对齐上报。
6. **抓了没人读的数组，还等它们全部回来**：`registry.ts:118-133` 给 `local_diversity` 列 10 个数组，实际只有 `coordination`、`neighbor_distances` 被读（`analysisVisualizations.tsx:540-541`）；`pairwise_similarity` 的 `distance_matrix`、各处的 `sample_indices`、trajectory 的 `event_indices` 同理。`Analysis.tsx:435-497` 用 `Promise.all` 等全部，`overviewArraysBusy` 又是整面板 loading 门 → 打开结果先"Loading bounded analysis arrays…"数秒（邻接表上限 20k×128）再丢掉 80% 载荷。 ◑
7. **可视化树零 memo，一次点选重扫 20k×16**：`Analysis.tsx:1379-1394` 每次渲染新建 props 与内联 `onSelect`；派生计算全在函数体（`analysisVisualizations.tsx:389-400` 16 个 `nums()` 各扫至多 20k、`:295-300` 256²≈65k 单元、`:620-621` 6 个 20k map），`PlotFrame` 无 memo → 每次 job 心跳都重跑并给 Plotly 传新数组引用。 ◑
8. **`trajectoryView.tsx` 的七个 useMemo 全部失效**：`:46-53` 在 render body 每次新建 `time/frames/series/coords/...` → `:54-117` 依赖恒变，拖 range slider（`onChange` 连发）就重跑 `stepPercentiles`（两次全量 argsort）+ events + visibleIndices。 ◑
9. `Explore.tsx`：原子表 `pagination={false}` + `dataSource={frame.atom_rows}` 且后端每原子一行不截断（`dataset_frame_service.py:56-76`）→ 1 万原子即 9 万个 `<td>`，换帧卡主线程数秒（全站其它表都有上限）；`frameMaxForce(frame)` 在 render body（`:149`）未 memo 且 effect 里 `:471` 再算一遍；`util/structure.ts:102` 与 `Explore.tsx:86` 每次渲染新建查找表。 ◑
10. 其他：`appUpdate.ts:79` 每个下载 chunk 写一次 store，而 `SettingsDrawer.tsx:28` 整店订阅 → 每个 chunk 重渲染整个抽屉；`SchemaForm.tsx:310-323` 的 `speciesToNumbers` 是手写的第二份元素表（止于 `U:92`，而全仓并无现成的 symbol→Z 表可复用），`.filter(n => n !== undefined)` 会把 Np–Og 静默丢掉、把 species 范围悄悄收窄；`statistics.py:368-370,411-412,514-517` 的 `element_counts` 用无界 Python int 列表并在汇总时整表复制（高熵合金 250k 帧可达数百 MB）；`extxyz.py:208-212,77-84` 对每条原子行做 1–2 次 UTF-8 编码仅为验证长度。
11. `result.heatmap`（`result_service.py:330-343`）与 `analysis.chunk`（`analysis_service.py:210-233`）都缺**总值**预算：前者只限列 ≤256、行数无上限（1e6 原子帧 → 必 INTERNAL_ERROR），后者先 `chunk.tolist()` 物化再等 `Server._encode` 判 8 MB。前者目前无前端调用者、后者实际可达性已被复核降级（邻接是 CSR 一维；真问题是长一维数组被静默截断到 20000 而不告知）。

---

## 五、契约与门禁（这类问题的系统性价值最高）

1. **数组响应的契约测试只断言"它是个数组"**：`test_backend_response_contract.py:44-49` 对 list 不记行内 key，`wire-contract.spec.ts:42` 又归一成 `keys: []` → `dataset.list / dataset.view.list / descriptor.list / result.list / analysis.list / job.list` 六个恰为**零行内断言**。已存在的真实漂移：mock 缺 `fingerprint_status` 与 `lineage`（`Sidebar.tsx:307,489` 在消费 lineage）、缺 `result/analysis_run_id/queue_position`（`stores/jobs.ts:277-278`、`JobsDrawer.tsx:77` 依赖）、缺 `memory_peak_bytes`（`analysisVisualizations.tsx:563` 读取）。修复只需一行：`_shape` 对 list 记首行 keys。 ◑
2. **mock 不实现任何校验/错误语义**，边界行为与真侧车系统性相反：未知 dataset id → `stats:null`（真侧车 `DATASET_NOT_FOUND`）；未知 check → 空 rows（真侧车 `INVALID_PARAMS`）；未知 `job.get` id → **凭空造一条永远 RUNNING 的行**（`preview.tsx:1096-1114`），拼错的 job id 在 e2e 里表现为"转圈"而不是失败；未知 `descriptor.describe` → 悄悄回落 `MOCK_DESCRIPTORS[0]`（DPA4 的 schema！）；`analysis.*` 提交完全无视参数（`:1247`）。所有错误码分支在 e2e 里等于没被测过。 ◑
3. **mock handler 抛异常时不产生响应帧**（`preview.tsx:1464-1475`）→ 请求永久悬挂，以 30 s Playwright 超时收场并指向错的文件；真侧车 `Server` 对 handler 异常一律回错误帧。 ◑
4. **发布包可以静默丢掉原生加速核**：`build_native.ps1:31-34` 找不到编译器 `exit 0`，`prepare_sidecar.ps1:11-12` 只看 `$LASTEXITCODE`，`backend/backend.spec:48-52` 用 `os.path.exists` 决定是否打包，`_native/` 又被 `.gitignore` 排除，`release.yml` 之后没有任何"DLL 在包里"的断言。叠加 `clean_command_environment`（`main.rs:368-378`）**未剥 `MDS_DISABLE_NATIVE`**（`native.py:59` 会读）→ 用户机器残留一个环境变量就能让出厂包退回 scipy 路径，即第四节第 2 条。 ◑
5. **Rust 侧失败原因进了不存在的 stderr**：release 是 `windows_subsystem="windows"`，`eprintln!`（`:177,195,216,226,271,275,294`）无去处；子进程 `stderr(Stdio::null())`（`:190`）丢掉 PyInstaller/导入期 traceback（发生在 `backend.log` 建立之前）。用户只看到"backend 离线"+ 必然再失败的重启按钮。附带：`kill()` 后从不 `wait()`（`:156,:244`）→ 非 Windows 每次重启泄漏一个僵尸；`backend_temp_dir()`（`:400-418`）只建不清。 ◑
6. **`verify_known_issues.py` 的守卫自己会死锁**：`:205-217` 的 45 s deadline 只在 `readline()` 返回后才检查，而 issue #1 的表现恰是子进程零输出 → 父进程永久阻塞、`finally` 的 kill 不执行，只由 workflow 的 45 分钟超时兜底。已知抱怨的"没人跑它 + main() 恒返回 0"两半已修（现 `:504-513` 会返回 1，`test_known_issues_gate.py` 与 workflow 在跑）。 ◑
7. **benchmark 的计时与内存指标不可信**：`run_benchmarks.py:197` 在 `import numpy`（`:35`）之后才 `setdefault("OMP_NUM_THREADS")`——实测对已加载的 BLAS 完全是空操作（3000³ matmul 0.100s→0.101s；import 前设 1 线程才是 0.813s）；`:160` `repeat=1 if samples>4000` 把冷启动单次当结果，还输出假的 `spread_seconds=[t,t]`；`peak_rss_mb`(`:67`) 计算后从未进任何输出行，`rss_high_water_mb`(`:68`) 其实只是末次 RSS，而 `README.md:36-37` 与 docstring `:9-10` 都按字面在解释它们。 ◑
8. 手抄常量：`_MAX_PREVIEW_POINTS = 20_000`（`analysis_helpers.py:18`）与 `feature_variance_schema:2`/`feature_correlation_schema:3`/`schema_version:2|3` 被手抄进 `preview.tsx:1194,1228,1238,527,559`——缓存身份依赖它们，后端 bump 而 mock 不动时历史恢复语义悄悄分叉，且不在 `test_wire_contract_parity.py` 射程内。

---

## 六、我已否证的误报（下轮别再提）

| 原结论 | 反证 |
| --- | --- |
| "`dataset.rename` 存入孤立代理对 → 该数据集所有响应永久失败 → 协议沉默" | SQLite 写入本身就抛 `UnicodeEncodeError`（实测），落不了库；`_encode:72` 的测量确实在 `try` 外，但 RPC 路径有 `_handle:220` 顶层 except 兜成错误帧，不会沉默。只降级为第五节之外的次要健壮性。 |
| "纯 QUEUED 作业永不计入 runningJobs" | `jobs.ts:247/:251` 的 filter 明确含 QUEUED（与 `JobsDrawer.tsx:59` 同口径），只是延迟到下一次事件。 |
| "`Descriptors.tsx:133` mount effect 会在后端未就绪时未处理拒绝" | 页面只在 `backendStatus==="ready"` 后挂载（`App.tsx:206-248`），ready 在 `system.info` 成功后才置位；缺 `disposed` 只剩无害的 StrictMode 双挂载。 |
| "`result.list` 的 `feature_space_signature` 在 mock 里漂移" | 它是真实字段（`result_service.py:92`）且已由 `tests/test_result_list.py:38` 钉住。 |
| "`analysis.chunk` 的 local_diversity 邻接表会超 8 MB 报错" | 邻接是 CSR 一维数组（`metrics/__init__.py:417-419`），唯一 2-D 是 `coords(n,2)`，≤0.8 MB；且 `Analysis.tsx:478` catch 后只见空图不见报错。 |
| "correlation 的最相关特征对静默错报" | 结论方向属实（只在按方差挑的子集里算），但**非静默**：`warnings:43`、`heatmap_limited/heatmap_feature_count:78-79` 已在 UI 渲染成"(heatmap subset)"，且 `heatmap_features` 用户可调（≤512）。 |
| "`main.py:202-203` 硬导入 scipy/dpdata 是启动脆弱点" | 二者都是 `backend/requirements.txt` 固定依赖（dpdata==1.1.0，ADR-19）。 |
| "`security.py:133` 对 junction/symlink 目录 `unlink()` 会残留或报错" | Windows + Py3.12 实测成功。 |
| "`ci.yml:60-62` 构建原生核这步恒绿" | `:56-58` 先装 MSVC，编译失败会 `throw` → 非零退出；仍成立的是 `build_native.ps1` 的"无编译器 exit 0"与 `test_native_stats.py:23-25` 整文件 skipif。 |

另外，此前有意保留的写法（encode 非有限值即失败、混合 pbc 压平、`lattice` 唯一边界来源、`_rmtree_quiet` 两份、viz 两个薄再导出、App 负责 IPC 重连、`_configure_stdio` 非死、`details` 不上线、`MAX_IMAGES_PER_AXIS` 钳制、engine 算法别名、`correlation.py:226` 可达）本轮均**未发现新证据推翻**。

---

## 七、建议的动手顺序

1. 三个"结果错但界面说成功"的问题：P0-1（export 丢 view_id）、P0-3（warm-start FPS 视图不进缓存身份）、P0-4（原子行伪装成结构）。每个都补一条回归测试。
2. P0-2（`if not arrays: return []`）、P1-5（`normalizePoints` 补三行）、P1-6（失败不写缓存）、P1-9（`cancelled` 分支 dispose）、P1-8（`recountRunning`）——都是一到数行的局部修复，收益立竿见影。
3. 契约层：`_shape` 记录数组首行 keys + mock 回错误帧 + `handler` 包 try + `build_native.ps1` 缺编译器改 throw + 剥 `MDS_DISABLE_NATIVE`。这类修复一次性把"e2e 全绿但生产不对"的整面墙补齐。
4. 科学口径类（P1-13…P1-19、strain 中心）需要一次集中决策：preprocess/scale 的默认值与回写、零方差判据、配位数与 `max_neighbors` 解耦、`scores` 语义。建议连同 `tests/test_analysis_engine.py` 一起钉。
5. 结构收敛（一次一个，各自独立提交）：指纹每请求算一次、cross 类型与数组名收成单一来源 + parity 测试、私有名越界改成公开只读 API、注释与实现逐条对齐或删注释、`result.heatmap`/`row_offsets_verified`/`granularity`/`_ANALYSIS_SCHEMA_VERSION`/`cache/` 这批"名存实亡"的按删或按用二选一。
6. 性能：`statistics` scipy 回退分块查询、`_local_neighbor_graph` 加 progress/取消并去掉逐候选 norm、`_analysis_row` 列清单去掉 blob、`ARTIFACT_ARRAYS` 按真实消费者裁剪、`Analysis.tsx`/`trajectoryView.tsx` 的 memo、`Explore.tsx` 原子表分页。

---

## 八、本轮已落地（第七节第 1–3 步 + 部分第 2 步）

全部改动已按下述顺序验证：**pytest 318 passed / 1 skipped**、**vitest 140 passed**、**eslint 干净**、**`tsc -b` 干净**、**Playwright 32 passed**、**`cargo test` 2 passed**。

| 修复 | 位置 | 钉住的测试 |
| --- | --- | --- |
| P0-1 导出走同一视图解析；`view_id` 进入导出缓存身份；格式错误的 `view_id` 在占行前拒绝 | `export_service.py`（`_view_id` + canonical + `_write_export`）、`Analysis.tsx` export payload | `test_export_resolves_the_selection_through_the_same_view_as_the_analysis`、`test_export_rejects_a_malformed_view_id_before_claiming_a_row` |
| P0-2 无可分页数组时直接返回，不再整表加载 | `artifact_service.py:237` | `test_an_unpageable_artifact_does_not_reload_the_descriptor_matrix` |
| P0-3 视图校验/取哈希从 `elif` 中解放，warm-start FPS 同样受校验并计入缓存身份；顺带把"无视图"的 scope 拼写统一成 `""` | `job_runner.py:84`、`:297` | `test_warm_start_fps_scopes_its_candidate_view_before_enqueue`（`selection_hash` 落库 + stale 视图在成 job 前拒绝） |
| P0-4 原子级 run 缺 verified offsets 时拒绝按结构解读；`row_offsets_verified` 从只写不读变成错误 details 的一部分 | `analysis_loader.py:249` | `test_atom_level_run_without_offsets_refuses_structure_mode` |
| 单一来源：`_view_id(params)`、`CROSS_DATASET_TYPES`（原本三处各写一遍且已漂移） | `analysis_helpers.py` | 由上述用例覆盖 |
| P1-5 color-by 字段透传 + 无数据时不再渲染死控件 | `analysisPreview.ts`（含 `hasColorByData`）、`Analysis.tsx:1347` | `Analysis.test.ts` 新增用例（含更新后的整行 `toEqual` 形状） |
| P1-6 取数失败的数组不再被缓存成"已取到" | `Analysis.tsx:478-483` | — |
| P1-8 作业计数改为跟随 store，而非只跟两个事件 | `stores/jobs.ts`（新增 `countRunning` + `subscribe`） | `jobs.test.ts` 新增 "running job badge" 两条 |
| P1-9 3Dmol 在 `cancelled` 分支释放刚创建的 viewer（两处） | `Explore.tsx`、`StructurePreview.tsx` | — |
| P1-2 保存视图时样本号不再冒充帧号：由散点与 `preview.selected` 共同建映射，无映射项丢弃并告警 | `Analysis.tsx:294-303` | — |
| P1-10/P1-11 表单默认值与显示一致（含缓存键）；数据集刷新不再清空已编辑参数 | `SchemaForm.tsx:302`、`Descriptors.tsx:153-159` | — |
| P1-12 设置写入按 key 串行 + 合并，最新值最后落库 | `features/analysis/persistence.ts` | `persistence.test.ts`（新文件，2 条） |
| 契约：数组响应比对首行 key 集合（空数组显式记 `null` 表示"未观测"，不放水） | `test_backend_response_contract.py`、`wire-contract.spec.ts` | 该 spec 本身；收紧后立刻抓出 mock 的三处真实缺字段 |
| mock 补齐 `fingerprint_status`/`lineage`、descriptor 行 5 个字段、job 行 `analysis_run_id`/`result`；handler 抛异常时按真实协议回 `INTERNAL_ERROR` 帧而非静默悬挂 | `preview.tsx`（`datasetRow`/`descriptorRow`/`jobRow` + 响应 try/catch） | `wire-contract.spec.ts`、`test_mock_backend_vocabulary.py` |
| 发布门禁：`build_native.ps1 -Require`（缺编译器即失败）+ 构建后断言 DLL 存在且不旧于源码；`prepare_sidecar.ps1` 与 CI 都传 `-Require` | `scripts/build_native.ps1`、`scripts/prepare_sidecar.ps1`、`.github/workflows/ci.yml` | 人工核对（PowerShell 路径不在 pytest 覆盖内） |
| 桥接：按前缀剥掉全部 `MDS_*` 环境变量（`MDS_DISABLE_NATIVE` 不再能悄悄关掉出厂包的加速路径） | `src-tauri/src/main.rs:368` | `cargo test`（现有 2 条不覆盖此函数，改动为纯环境清洗） |

实施中新增两条"看起来是 bug 但不是"的结论，已并入第六节：`Server._encode` 的孤立代理对场景无法落库（SQLite 拒写）；mock 的 `NO_HANDLER` 错误帧形状与 `frames.response_err` 一致（缺的只是"异常时不发帧"）。

一个必须知道的副作用：`collectDefaults` 现在会为所有带 `default` 的参数赋值，descriptor 的 `cache_key` 由提交参数计算，因此**升级后第一次重跑旧 run 的参数组合会重新计算一次**（旧 run 的 `parameters_json` 里少这些键）。换来的是"屏幕上显示什么就算什么"，以及可选参数被显式改回默认值时不再产生第二个 run。

尚未动的（第七节第 4–6 步）：科学口径类需要一次决策（`coverage` 的默认尺度、零方差判据阈值、配位数与 `max_neighbors` 解耦、`acquisition.scores` 语义、strain 的缩放中心）；性能与结构收敛各条保持原样，其中 `statistics` 的 scipy 回退内存放大现在被发布门禁挡在了"出厂一定有原生核"之后，但非 Windows/源码构建仍会踩到。
