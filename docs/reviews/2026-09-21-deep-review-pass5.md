# 深度审阅（第五轮）2026-09-21

范围：全仓，但切片与第四轮**不重叠**：导出与物化落盘、缓存与生命周期、第四轮没碰的数值与面板语义、契约与死重量。
方法：4 个审阅 agent 分片（导出与回收 / 并发与生命周期 / 数值与面板 / 契约与死重量），每条候选结论由我重读代码或重跑实测后才进入下面的清单；agent 自己否决的部分一并记在「反证记录」。
基线 `9a6b648`（第四轮收尾）。开轮门禁：pytest 381 passed / 1 skipped；vitest 203；Playwright 44；tsc / eslint 干净；cargo test 4 passed。
环境提醒（与第四轮相同）：跑测试必须用 `.venv/Scripts/python.exe`，并且**始终带 `tests` 路径**——裸跑 `pytest` 会把 `src-tauri/resources/backend/_internal/**/tests` 一起收集，报三十多个 `ModuleNotFoundError: hypothesis`，那是打包产物不是仓库坏了。

## 已落地

| 提交 | 主题 | 验证 |
| --- | --- | --- |
| `35924f2` | 5-A1..A5 落盘安全：固化「拒绝覆写」的分支反过来删掉用户文件（refusal 落在 `try` 里，被自己的 `_discard_partial_output` 接走）；空占位改为可复用（排队中被取消的固化永久占死那条路径）；导出补上 materialize 早就有的「不得落在源数据集内」守卫（并把该比较搬进 `security.path_within`，逐层用 `same_lexical_path`，Windows 大小写不再漏）；同一目标上的两个活作业按路径串行（注释里「会被去重」只对同参数成立）；extxyz/DeepMD 分支不再丢弃 writer 自己数出的帧数 | 新用例 `test_a_refused_materialize_destination_survives_the_failed_job` 把改动前形态跑出 `FileNotFoundError`（用户的文件真被删了）；`test_two_live_exports_to_one_destination_take_turns` 同路径 `most_live==1`、异路径 `==2`；pytest 381 → 383 全绿 |
| `c9cf613` | 5-B1 指纹缓存的 TTL 用「计算开始前」的时间盖章 → 只要 walk 超过 2 s 就写进去即过期，`dataset.list` 的防抖对慢源永不上岗；5-B2 `adapter_for` 的检查-然后-写跨三个线程池裸奔，冷数据集被并发整份重复载入 | 合成 12 060 文件树实测第二次调用 2 735 ms → 1.6 ms；四线程同数据集 4 份副本 → 1 份且四者同一对象；两条门禁各自反向验证（假时钟报 `walks == 1`、无锁报 `4 copies`） |
| `<hash5>` | 5-D1/5-D2：协议文档的四处假承诺改回实现真正做的事（版本不匹配只回一帧 `id:null` 的错误帧并继续服务，没有 exit 2；`dataset.get` 不返回 `fingerprint_valid`；不存在 `JOB_CANCEL_UNSUPPORTED`，取消已结束作业是 `{ok:true, already_finished:true}`；用户可见文案归后端 `_PUBLIC_MESSAGES`，前端不按 code 映射）；§6 补齐 `BUSY`/`DATASET_BUSY` 并把标题的 24 改成 25；`descriptor.submit` 补 `num_threads`/`force`/`cache.in_flight`，`dataset.frame` 补 `ghost_parents`；§5 末尾写下 registry 别名 RPC 的真实规则 | 新门禁 `test_the_protocol_document_lists_every_declared_error_code_once` 把文档代码块与 `errors.py` 声明集双向比对并核对标题计数；把标题改回 24 即报 `the heading counts 24, the block lists 25, errors.py declares 25` |
| `11b4bf4` | 5-C6：drift 预览写下 `mmd_reference_rows`/`mmd_query_rows`，面板把这两个数摆在三个核估计之后，方法指南说明距离类覆盖全部 query 行、MMD/质心/协方差只用每侧 ≤`distribution_samples` 行 | 新用例：60 行数据在 `distribution_samples=25` 时报 25/25，而 covered+marginal+out_of_coverage 仍是 60；不 bump（旧行缺键即按 `Metrics` 既有规则不显示该芯片）|
| `463431d` | 5-B3 `Analysis.tsx` / `DescriptorResults.tsx` 的 `result.list` 一族响应加世代号（旧答案不得写表、不得改选中的 run、不得把上个数据集的 run 写进持久化设置）；5-B4 `App.tsx` 区分「我要求的那次 exit」与「替换进程没起来的那次」，后者立刻报「后端无法重启」并放开 Restart 按钮；5-B5 `_group_labels_cache` 的命中 touch 与逐出收进一把锁 | 新 App 用例走真按钮与两次 exit：撤掉修复后第二次 exit 静默、断言报 `expected '…' to contain 'could not be restarted'`；vitest 208、Playwright 44、pytest 383。5-B3 的两处守卫只有读码验证，无门禁 |
| `7f53205` | 5-C1 特征方差详情图：柱是全体有限值、线是**故意保留全部异常值**的有界样本，却按柱的总数定标 → 同一根轴放两个总体；改为按样本定标并写明 n/N。5-C3 `restore` 说「早于控件的行是 centered 算的」是假的（`git show f86a61c~1` 里后端一直默认 standardized），还原旧行会换掉统计量。5-C4 相似度矩阵上方写着距离的 min/max（cosine 实测「Maximum 1.86」而画出的值最大 0.76）。5-C7 指南把「常量」定义成极差 ≤ 1e-12，而 B-3 之后规则是相对量级（1e6 上抖 4.6e-8 即常量） | vitest 203 → 207（`buildKde` 定标随第三个参数线性、无散布不出线；`matrixExtent` 含非有限格与空输入），restore 两向都断言；tsc / eslint / Playwright 44 干净 |

## 待修（已核实，按批排列）

### 第 1 批 · 需要一次失效授权（本轮唯一被卡住的两条）

两条都改**已存结果**的内容，而 `ANALYSIS_ALGORITHM_VERSION` 已在第四轮过到 "studio-analysis-7"、额度用尽。修法是确定的，缺的是「要不要再来一次 bump」的决定。

| # | 位置 | 问题 | 已有测量 |
| --- | --- | --- | --- |
| 5-C5 | `analysis/algorithms/_common.py:749-751` | 同一文件里 `_nearest_distances:217-225` 已经写明「按位置丢自身是错的：重复行会打平，`[1:]` 丢掉真邻居、把自己的 0 距离留下」，并改成按身份排除；但 `_aligned_space_metrics` 的 kNN 重叠仍用 `argsort(...)[:, 1:k+1]`。含完全重复描述符行的数据集里，Descriptor Comparison 的 kNN overlap 与 Parameter Sensitivity 的 `neighbor_overlap` 一起虚高 | n=200/k=10：40 行重复 → 0.641 vs 0.631（+1.0 个百分点）；100 行重复 → 0.8945 vs 0.8820（+1.25）；上界是重复占比 × 1/k |
| 5-C8 | `analysis/sampling/engine.py:172` 与 `sampling/fps.py:50-52` | `FPSResult` 承诺「`indices` 按选择顺序，`selection_distances[k]` 与之一一对应」，引擎却把 `selected_indices` 排序后再写工件，距离数组没跟着排。两个数组都作为独立 `.npy` 落盘、可经 `analysis.chunk` 取回，按行拼接就配错 | `tests/test_fps_sampling.py:228-231` 的选择顺序 2→0→4→1→3 与输出 `[0,1,2,3,4]` 直接对照即证；全仓无一条测试同时检查这两个数组。诚实说明：目前 `registry.ts:144` 只读三条 coverage 曲线，面板不读 `selection_distances`，受影响的是导出/直接取工件的人 |

### 第 2 批 · 前端生命周期 —— 四条已由 `463431d` / `11b4bf4` 落地，见上表

同一条门禁事实值得记下：`tests/test_backend_response_contract.py` 在全量并跑时曾失败一次、单独跑与重跑全量都通过 —— 它经 `conftest.BackendProcess.read_line(timeout=30)` 等 sidecar 握手，机器被其他会话占满时 30 s 会到。这不是契约漂移而是这台共享机器上的负载抖动，报告在此登记以免下次有人当真去改形状。

| # | 位置 | 问题 | 已有测量 |
| --- | --- | --- | --- |

### 第 3 批 · 门禁质量 —— 两条已由 `cafc7ad` 落地

同一条测试文件里唯一涉及质量的断言（`trustworthiness > 0.9`）此前**不可能失败**：
实测删掉全部斥力 0.9287、直接返回 PCA 初值 0.9326、σ 二分退化 0.9547、`_ab_params → (1,1)`
（即 `min_dist` 被完全忽略）0.9580 —— 四个破坏实现全都跨过 0.9，而 9 条测试里只有这一条看质量。
现在阈值收到 0.95（本实现 0.9607，seed 之间 0.9592-0.9595，n=1200 0.9578，PCA 参考 0.9328），
并新增一条 `min_dist` 单调性：中位最近邻距离 0.1859 → 0.2052 → 0.3872 → 0.4989（0.05/0.1/0.5/0.99）。
注入「`_ab_params` 返回固定 (1,1)」后，前者仍绿（0.958）、后者报 `assert False` 并把四个 spread 打出来。
另一条：`_write_export` 的 extxyz/DeepMD 分支此前把 writer 返回的帧数丢掉、改回 `len(frames)`，
所以「记录数 = 文件字节」这条 C-11 的契约在帧格式上无任何人看守；新用例把 writer 换成返回 3，
断言行里记的是 3，恢复旧写法即报 `assert 1 == 3`。

### 第 3.5 批 · 报告为「过度防御、可删」，待我逐条复核

agent 只给了读码证据，我自己还没跑过，所以先记账不动手：

| # | 位置 | 主张 | 复核要到什么程度 |
| --- | --- | --- | --- |
| 5-B6 | `services/job_service.py:45, 50-52, 83-84, 185` | `JobContext.detached` 是同一条件的第三道闸：`cancel()` 先 `ctx.cancel()` 再 `ctx.detach()` 再 `_finalize()`，故 `detached` 恒蕴含 `_cancelled.is_set()`；`progress()` 想挡的「行已结算后仍写进度」实际由 `_update_progress` 的 `WHERE status IN ('QUEUED','RUNNING')` 挡住 | 需要确认没有第四条路径只 detach 不 cancel，且删后亚毫秒窗口里多出的那条 0 行 UPDATE 无副作用 |
| 5-B7 | `storage/database.py:164` | `_write_lock` 是 `RLock` 而无人重入；更糟的是它让「在 `transaction()` 里调 `self.db.execute(...)`」这种提前提交外层事务的写法静默通过 | 需要确认两个 `transaction()` 使用点确实不回回调 `db.*`，然后换成 `Lock` 看测试是否全绿 |

### 第 4 批 · 契约与死重量 —— 5-D1/5-D2 已由 `<hash5>` 落地，其余逐项都需点头

| # | 位置 | 问题 | 已有测量 |
| --- | --- | --- | --- |
| 5-D3 | `dataset_service.py:150-154` + golden + `preview.tsx:1008` | `fingerprint_status` 每次 `dataset.list` 都算并上线，`DatasetMeta` 类型里没有它 → TS 不可能读；golden 把它钉成必发键，第三轮还倒过来给 mock 补了一份。同一事实 `cache_valid` 已经表达，且有三处真读者 | 全仓 4 处命中全为生产端/门禁/旧文档，前端读取 0 |
| 5-D4 | `main.py:34/76/87-90`、`analysis_helpers.py:21-27`、`analysis_runs.schema_version/algorithm_version` | 版本编号里真正 gate 东西的是 `FINGERPRINT_VERSION`、`STATS_VERSION`、`ANALYSIS_ALGORITHM_VERSION` + `FEATURE_*_SCHEMA` 四处；`ANALYSIS_API_VERSION`（唯一没被词表门禁钉住的 mock 抄本）、`analysis_dependencies`（每次 `system.info` 跑两次 `importlib.metadata.version`，设置面板不显示）、`_ANALYSIS_SCHEMA_VERSION`（注释自己承认「nothing compares it」）、两列 `schema_version`/`algorithm_version`（上线、进类型、零读者）都不比较任何东西 | 常量名 + 列名 + JSON 键名全仓 grep（排除 vendored `resources`） |
| 5-D5 | `analysis/metrics/__init__.py:558-631` | `analysis.trajectory` 服务端一半参数没人能发（面板把范围/方法/灵敏度全放浏览器重算，`submission.ts` 恒发 `{}`）：`timestep`/`time_unit` 分支零发送者零测试而 `trajectoryView.tsx:160` 还在读它；`preview.events`（≤200 条 × 8 字段）与 9 个 preview 键零读者；数组 `indices` 与 `sample_indices` 是同一份数据落两遍，`speed`/`event_indices` 不在读取清单 | 脚本抽出 trajectory 的键逐个 grep 前端；只有 `test_analysis_ipc.py`/`test_analysis_engine.py` 发过这些参数 |
| 5-D6 | `metrics/__init__.py:337`、`correlation.py:72`、`analysis_service.py:326` | 每个分析的修订号抄两份：进缓存身份的那份是常量，展示用的 `"schema_version"` 是同一个数的第二处字面量，`property_correlation_schema` 更连常量都没有。词表门禁把 mock 抄本钉在常量上，bump 时先炸的是两条手写 `== 2` 的断言 —— 既不 gate 缓存，也说不清哪个是真编号 | 6 处 file:line + grep |
| 5-D7 | `analysis_service.py:230`、`export_service.py:64`、`analysis_loader.py:461,483-487`、`result_service.py:336-340` | 四个入参永远走默认：`array_name`/`output_format` 是 `array`/`format` 的第二个拼法（无第二个客户端，契约却出现两种写法）；`block_weights` 无人能发却参与 canonical params 与缓存身份（一个没人能设的键槽位）；`max_features` 只被本就无调用者的 `result.heatmap` 读 | 抽出后端 84 个 `params.get` 名，对前端/e2e/tests/scripts/benchmark 逐个 `grep -w`，这四个命中 0 |
| 5-D8 | `statistics.py:718-719`、`dataset_service.py:694`、`protocol.ts:90-91,342` | 写而不读：`atoms_per_structure` 与其 summary 每次扫描都算、进 `stats_json` 常驻、类型里还是必填，前端零读取；`analysis_runs.stale_reason` 后端已经算好「源文件指纹变了」这句，但没人显示，所以一行变 STALE 时用户只看到「STALE」；`descriptor_runs.error_message` 三处 UPDATE 写入，只有 SQL 测试读 | 逐 key `grep -rw` 前端非测试代码 |

### 第 4 批的失效说明（决定顺序时用得上）

5-D3/D8 要删的是**已存载荷的键**：`Stats` 侧去动 `STATS_VERSION`（额度已在第四轮花掉），`dataset.list` 侧去动 golden。5-D4/D5/D6/D7 只删生产端与类型声明，不动任何计算结果，因此不需要 bump。也就是说第 4 批里真需要新授权的只有 `Stats` 那两个键 —— 而它们本来就是零读者，留着不花钱。

## 落地时对本报告的自我修正

1. **agent 报的「`_json_safe` 那类全树遍历可以顺手收窄」我没接。** 第四轮已把同一条判断记为独立决定；本轮它第三次出现，说明这类“看起来免费”的删除其实每次都要重新买一次不变量。
2. **5-A2 的修法换了一次。** 报告原打算「同一目标上有活作业就拒绝」，读下来发现覆写一个**已完成**导出的目标本就是 `analysis.export` 的文档行为，拒绝会新增一种用户无法行动的失败；改成按目标路径串行，语义不动，交错消失。
3. **「排队中被取消」的占位（5-A3）没按 agent 建议改成 runner 内占位。** 那会把「路径已被占」的答复推迟到作业启动，用户在对话框里选的本地数据集会被多写一次指纹；改成让重试复用自己的空占位，规则与写入时那条 `_refuse_filled_destination` 合并成一个 `_destination_is_empty`。
4. **5-C1 的修法不是「让 KDE 换一份无偏样本」。** 后端保留全部异常值是刻意的（长尾在详情面板里可见），换样本等于删功能；真正的错位只有「曲线按柱的总数定标」这一处，于是改定标 + 写明样本，不动载荷、不动任何已存数字。

### 计数（把这页当账本时用）

四个 agent 交回 29 条候选，我自己补了 1 条（5-N3：writer 计数无测试看守），共 30 条：**已落地 11**（5-A1..A5、5-B1/B2、5-C1/C3/C4/C7），**待修 18**（第 1 批 2 条需失效授权、第 2 批 4 条、第 3 批 2 条、第 3.5 批 2 条待复核、第 4 批 8 条），**记录为故意不做 1 条**（5-A6：`_write_export` 里三到四次同链 reparse 展开 —— 那是噪声而非缺陷，为整洁删一道路径守卫不划算），另有 agent 自否的 13 条进下面的反证记录。

## 反证记录（不要再报）

- **`Server._encode` 为量帧尺寸多做一次 UTF-8 编码**：20 万点帧 `json.dumps` 145.6 ms、再编码 1.2 ms（约 1 %），不值得动。
- **`_serve_polled` 的 `pending += chunk` 二次方累积**：只在 warmup 期，且实际请求帧 ≪ 64 KB，够不到。
- **`state.child` 锁与 `stdin.write_all` 的管道死锁**：reader 线程只在 stdout 已 EOF 后才取 `child` 锁，平时 stdout 一直被排空，构不出环。
- **`Server._control_pool` 单 worker 头部阻塞**：找不到能长时间持有 `_write_lock` 的语句（`dataset.remove` 的 `remove_managed_tree` 在锁外）。
- **分析插件共享可变状态**：`analysis/` 全包 `self.x = ...` 只命中 `models.py:157` 的 `__post_init__`，构造期一次赋值。
- **BLAS 线程数破坏投影可复现性**：`threadpoolctl` 限 1/2/8 线程下 4000×4000 float32 Gram 矩阵逐位相同（差异元素 0/16 000 000）。
- **`_visual_pca_components` 的 `svd_solver="randomized"` 污染解释方差标注**：七种形状最大偏差 6.4e-6（48×100 近平坦谱），其余 ≤3e-16；`min(shape) ≤ 4` 走精确 SVD。
- **Python/TS 统计约定不一致**（分位数、MAD、z-score、样本偏移）：逐条对拍为同源 —— 前端 `sortedQuantile`/`trajectoryThreshold` 用 numpy 默认 `linear` 插值，`stepStats` 中位数取两值平均、std 用 ddof=0，`stepPercentiles` 的 `(rank+1)/n` 与后端 `argsort(argsort())` 同为序数秩，mad/zscore 分支直接取后端预览值。
- **`Math.min(...identityBounds)` 大数组爆栈**：V8 到 65 000 参数仍正常，125 000 才抛；`arrays.targets` 受 `limit: 20_000` 约束，不可达。
- **ARI 可为负而 trajectory/sensitivity 的 y 轴裁到 [-0.05, 1.05]**：实测两个无关空间 ARI = −0.011 仍在轴内，且该面板比的是同一描述符的不同设置，未构造出可达裁切。
- **`dataset.remove` 的视图/统计/血缘残留**：`database.py:117-138` 三条外键皆 `ON DELETE CASCADE`（parent 为 `SET NULL`）且 `PRAGMA foreign_keys=ON`。
- **`sweep_abandoned` 与僵尸行扫描先后**：`main.py:184` 先把遗留行落 `CANCELLED`，`191` 再清目录，没有漏一代。
- **`artifact_service._rows_from_artifact` 丢 `view_id` 作用域**：唯一能走到它的 `pairwise` 预览本就返回 `[]`。
- **报告导出对非 COMPLETED 分析写全 null**：UI 只在 `loadAnalysis` 成功后设 `analysisId`，产不出该输入。
- **`_claim_analysis_run` 的 abandoned 行重认领能让两个 runner 抢结算**：构造不出可达时序（`_settle_linked_runs`、`_sweep_zombie_runs`、提交失败回滚都会连带结算或删除）。
- **`threadpoolctl` 是死依赖**：`job_runner.py:304` 函数内延迟导入，在用。
- 已确认干净、不必再查：方法词表十向钉死（`test_mock_backend_vocabulary.py`，五个未 mock 方法是具名例外）；golden 与 `DescriptorInfo`/`DatasetView`/`job.list`/`Stats`/`FramePayload` 逐键相等；`job.progress`/`job.finished` 载荷与文档一致；900 条 `zhDict` 零孤儿；`frontend/src` 全部 export 有引用；两个 requirements 清单全部有 import；`scripts/` 五个各有跑它的地方；`registry.ts` 的数组反向门禁无假名（除 5-D5 的「后端多产」）。

## 明确留给你、不动的三件（与第四轮相同，未再核查）

1. `preview.tsx`（1821 行，实为 4 个文件）不拆。
2. `scripts/verify_native_geometry.py` 能跑但没有任何东西跑它。
3. `scripts/create_analysis_report_pdf.py` 246 行、不 import 后端、`reportlab` 未声明、引用数 0。
另加第四轮那条独立的：`preview_service._build_preview` 末尾的全树 `_json_safe` 约占该函数 80 % 运行时，收窄约 2×，代价是一条看不见的不变量。
