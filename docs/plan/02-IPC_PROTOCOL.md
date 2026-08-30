# 02 · IPC Protocol（protocol_version = 1）

> 状态：定稿（M0 入口条件）
> 依据：设计文档 §33–39、ADR-8/16；错误码基线 §44

## 1. 传输层

- **stdio + NDJSON**：UTF-8，一行一帧，`\n` 结尾；无帧长头；单帧不超过 8 MB（超大负载一律走文件 + 引用）。
- Backend 的 stdout 只用于协议帧；日志一律写 stderr / 日志文件。
- stdin 关闭（父进程退出）→ backend 优雅退出（flush 落库，exit code 0）。

## 2. 启动握手

backend 初始化完成后，**第一帧**输出：

```json
{"protocol_version":1,"event":"backend.ready","data":{"backend_version":"0.1.0","mdescriptor_version":"0.2.7","mdescriptor_api_version":1,"mdescriptor_baseline_version":"2","mdescriptor_descriptor_info_schema_version":3}}
```

前端收到前，计算相关 UI 保持 disabled。`system.info` 可随时查询同等信息。

## 3. 帧格式

```json
// request
{"protocol_version":1,"id":101,"method":"dataset.list","params":{}}
// success
{"protocol_version":1,"id":101,"result":{}}
// error
{"protocol_version":1,"id":101,"error":{"code":"DATASET_NOT_FOUND","message":"Dataset does not exist.","details":{}}}
// event
{"protocol_version":1,"event":"job.progress","data":{"job_id":"...","progress":0.67,"completed":6700,"total":10000}}
```

规则：
- `id` 由前端生成（自增整数），响应必须原样携带；**允许多请求并发**（id 关联，ADR-16）。
- 事件独立下行，可能与响应交错；事件无 `id`。
- `protocol_version` 不为 1 → 回 `PROTOCOL_VERSION_MISMATCH` 错误帧并退出（exit 2）。
- method 未知 / params 非法 → `INVALID_PARAMS`；job id 不存在 → `JOB_NOT_FOUND`。

## 4. Job 模式（ADR-8/16）

预计 > 1s 的方法**同步返回 `{"job_id": "..."}`**（error 帧仅用于立即校验失败），随后：
- `job.progress` 事件：`{job_id, progress(0–1), completed, total, message}`
- 终态事件 `job.finished`：`{job_id, status: COMPLETED|FAILED|CANCELLED, result: {...}|null, error: {...}|null}`
- 查询：`job.list {dataset_id?, status?}`、`job.get {job_id}`、`job.cancel {job_id}`（无 cooperative_cancel 时返回错误码 `JOB_CANCEL_UNSUPPORTED`→ 前端显示 Cancel unavailable）。

Job 状态机：`QUEUED → RUNNING → COMPLETED | FAILED | CANCELLED`。

## 5. 方法目录 v1

| method | params → result | 异步 |
|---|---|---|
| `system.info` | {} → {backend_version, mdescriptor_version, mdescriptor_api_version, protocol_version, platform, data_dir, cpu_threads} | 否 |
| `dataset.list` | {} → [{id,name,format,source_path,number_of_frames,elements,properties,periodicity,fingerprint,file_size,created_at,last_scan_at,cache_valid}] | 否 |
| `dataset.register` | {path, format?: "deepmd"\|"extxyz", name?} → {job_id}（扫描+统计入 cache，统计含 health 数据健康指标） | 是 |
| `dataset.remove` | {id} → {ok}；级联删除统计缓存与 descriptor runs/results，不碰源文件 | 否 |
| `dataset.rename` | {id, name} → DatasetMeta（仅改显示名，name 首尾空白被裁剪） | 否 |
| `dataset.get` | {id} → Dataset + {fingerprint_valid, stats} | 否 |
| `dataset.statistics` | {id} → stats（直方图 bins + 摘要 + property availability + health：missing_values/invalid_cell/duplicate_structures/extreme_force 帧；百分比前端按 structures 计算）；缓存失效**或缓存缺 health**（旧版缓存）时自动触发重算 job；同数据集进行中的扫描 job 会被复用（Overview/健康栏并发调用共享一个 job） | 否/是 |
| `dataset.rescan` | {id} → {job_id}；无视缓存有效性强制全量重扫（右侧 Data Health 面板 Rescan 按钮）；与进行中的扫描 job 去重 | 是 |
| `dataset.frame` | {id, index, bond_cutoff?} → {index,natoms,formula,xyz,atom_rows,energy,energy_per_atom,force_max,volume,pbc,cell,ghost_count,bond_cutoff}；`bond_cutoff` 为 0.1–10 Å，缺省 2.4 Å | 否 |
| `descriptor.list` | {} → [{name,display_name,description,schema_version,descriptor_version,level,backend,execution_engine,category,capabilities,input}] | 否 |
| `descriptor.describe` | {name} → schema 全文（含 input/execution/asset/parameters） | 否 |
| `descriptor.submit` | {dataset_id, descriptor_name, parameters, scope: "frame"\|"dataset", frame_index?, output_dtype?} → {job_id, cache?: {existing_run_id, cache_key}} | 是 |
| `result.list` | {dataset_id?, descriptor_name?} → [runs]（含已生成结果的 `shape`） | 否 |
| `result.get` | {run_id} → metadata + 摘要（不含大数组） | 否 |
| `result.remove` | {run_id} → {ok}；级联删除该 run 的 analysis_runs 与关联 jobs 行，并尽力删除磁盘结果/分析目录；run 处于 QUEUED/RUNNING 时拒绝（`RESULT_INCOMPATIBLE`，先取消 job） | 否 |
| `analysis.pca` | {run_id, mode?: "structure"\|"atom"} → {job_id, analysis_id, cache?}；同一 descriptor run + mode 的已完成 `pca.json` 直接命中缓存（`job_id: null`，`cache.existing_analysis_id`）；进行中的同键任务复用其 job；mode 缺省 structure（每帧一点，原子/配对行均值池化）；atom 模式每个原子/配对行一点并带 frame/atom 索引，超大结果均匀降采样至 ≤20k 点 | 否（缓存命中）/是（需计算） |
| `result.get_pca` | {analysis_id} → pca.json 全文（points/explained_variance/x_label/y_label/mode，点数=帧数或原子行数，非大数组） | 否 |
| `result.heatmap` | {run_id, frame_index, max_features?} → {atoms, features, values, atomOffset}；max_features 硬上限 256（§25） | 否 |
| `settings.get` | {key} → {key, value\|null}（settings 表 KV） | 否 |
| `settings.set` | {key, value} → {ok} | 否 |
| `engine.check_update` | {} → {installed, latest, has_update, status: idle\|checking\|up_to_date\|available\|error\|unsupported, error?, restart_required?}；后台线程查 PyPI，完成后再次广播 `engine.update.state` 事件（同结构） | 否（后台线程） |
| `engine.update` | {version?}（缺省用 latest）→ {job_id, target_version}；pip 升级 job，终态后需重启后端生效；frozen 构建报 `ENGINE_UPDATE_UNSUPPORTED` | 是 |
| `job.list` / `job.get` / `job.cancel` | 见 §4 | 否 |

### 5.1 Analysis extension

Analysis API 统一使用同一结果模型：计算型方法立即返回
{job_id, analysis_id, cache}；缓存命中时 job_id=null。analysis.list/get
只返回元数据和 preview，不加载大数组；analysis.preview 上限为 20,000
点；analysis.chunk 通过数组名、offset、limit 和可选列范围分页读取。

| method | 关键参数与结果 |
|---|---|
| analysis.list/get/delete/preview/chunk | 通用 artifact 生命周期；结果目录必须有 completed manifest |
| analysis.umap / analysis.tsne | run_id、mode、seed=42；UMAP 默认 n_neighbors=15/min_dist=0.1，t-SNE 默认 perplexity=30 |
| analysis.neighbors / analysis.similarity / analysis.pairwise | k 默认 10；Euclidean/Cosine；neighbors 返回 n×k，pairwise 仅返回确定性有界抽样矩阵（UI 默认 ≤400 samples） |
| analysis.cluster | algorithm=kmeans/dbscan/hdbscan/agglomerative；cluster 数默认 6；返回 labels 及可选 centers/probabilities |
| analysis.outlier | algorithm=knn/lof/isolation_forest/mahalanobis；contamination 默认 0.01；返回 labels/scores |
| analysis.fps / analysis.sampling / analysis.acquisition | FPS、random、stratified、cluster_representative、per_element；acquisition 在 reference novelty pool 内做 diversity sampling，不调用模型推理 |
| analysis.coverage / analysis.overlap / analysis.drift | reference_run_id/query_run_id；nearest coverage、near-duplicate overlap、MMD/centroid/covariance drift，并返回有界 joint projection |
| analysis.compare | sample IDs 对齐；比较 pair-distance Pearson/Spearman、kNN overlap、PCA topology、ARI 和 effective dimension；同 feature count 时追加 feature delta |
| analysis.feature_variance / analysis.feature_correlation | variance、zero-variance、redundancy summary、Top-K pairs 和 ≤512 feature 的有界 correlation heatmap |
| analysis.effective_dimension | eigenvalues、explained variance、participation ratio 和 90/95/99% 阈值 |
| analysis.property_correlation | energy / force / volume 等已存在物理量；feature correlation、cross-validated Ridge 和 descriptor-distance/property-delta correlation |
| analysis.local_diversity | 强制 atom mode；按 element 做 neighbor-distance category、clustering 和 effective dimension summary |
| analysis.kernel | linear/cosine/polynomial/RBF；有界 kernel matrix、centered eigenvalues 和 effective rank |
| analysis.trajectory / analysis.sensitivity | 显式 frame range/timestep；sensitivity 只比较同一种 Descriptor 的 Completed Run；不同 descriptor 使用 analysis.compare |
| analysis.export | JSON/CSV identity/meta，DeepMD/extxyz 子集；禁止修改 source_path |

新分析输入必须是当前 fingerprint 对应的 Completed Descriptor Run。NaN/Inf、
空输入、样本不足、未声明且未验证的 atom row_offsets 返回结构化错误；
zero-variance 特征可确定性忽略并记录 warnings。完整数组以 float64 落盘。

## 6. 错误码全集（24）

```text
DATASET_NOT_FOUND        DATASET_CHANGED         INVALID_DATASET
UNSUPPORTED_FORMAT       UNSUPPORTED_PERIODICITY MDESCRIPTOR_INCOMPATIBLE
DESCRIPTOR_CONFIGURATION_ERROR                   MODEL_NOT_FOUND
OUT_OF_MEMORY            JOB_CANCELLED           RESULT_INCOMPATIBLE
INTERNAL_ERROR           PROTOCOL_VERSION_MISMATCH       JOB_NOT_FOUND
INVALID_PARAMS           ENGINE_UPDATE_UNSUPPORTED
ANALYSIS_NOT_FOUND       ANALYSIS_DEPENDENCY_MISSING
ANALYSIS_INPUT_INVALID   ANALYSIS_INSUFFICIENT_SAMPLES
ANALYSIS_STALE           ARTIFACT_INVALID
EXPORT_FAILED
```

（`JOB_CANCEL_UNSUPPORTED` 由 job.cancel 以 `INVALID_PARAMS` 携带 details 表达，不单列。）

错误帧 `message` 为面向开发者的英文；用户友好文案由前端按 code 映射；traceback 只进日志。

## 7. Sidecar 生命周期

- **dev**：Tauri 直接 spawn `.venv\Scripts\python.exe -m mdescriptor_studio_backend`（cwd=backend/，env `MDS_DATA_DIR` 可覆盖数据目录）。
- **release**：spawn PyInstaller 产物 `binaries/backend-x86_64-pc-windows-msvc.exe`（externalBin）。
- Rust 侧：stdout 按行转发给 webview（事件 `backend-message`）；`backend_send(line)` 命令写入 stdin；子进程退出 → 事件 `backend-exit`，前端置后端不可用并提供重启入口。
- 应用退出：关闭 stdin → 等待 ≤ 3s → kill。
