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
{"protocol_version":1,"event":"backend.ready","data":{"backend_version":"0.1.0","mdescriptor_version":"0.2.5","mdescriptor_api_version":1}}
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
| `dataset.list` | {} → [{id,name,format,source_path,number_of_frames,elements,properties,periodicity,fingerprint,file_size,created_at,cache_valid}] | 否 |
| `dataset.register` | {path, format?: "deepmd"\|"extxyz", name?} → {job_id}（扫描+统计入 cache） | 是 |
| `dataset.remove` | {id} → {ok}；级联删除统计缓存与 descriptor runs/results，不碰源文件 | 否 |
| `dataset.rename` | {id, name} → DatasetMeta（仅改显示名，name 首尾空白被裁剪） | 否 |
| `dataset.get` | {id} → Dataset + {fingerprint_valid, stats} | 否 |
| `dataset.statistics` | {id} → stats（直方图 bins + 摘要 + property availability）；缓存失效时自动触发重算 job | 否/是 |
| `dataset.frame` | {id, index} → {index,natoms,formula,xyz,atom_rows,energy,energy_per_atom,force_max,volume,pbc} | 否 |
| `descriptor.list` | {} → [{name,display_name,level,backend,category,capabilities}] | 否 |
| `descriptor.describe` | {name} → schema 全文（含 input/execution/asset/parameters） | 否 |
| `descriptor.submit` | {dataset_id, descriptor_name, parameters, scope: "frame"\|"dataset", frame_index?, output_dtype?} → {job_id, cache?: {existing_run_id, cache_key}} | 是 |
| `result.list` | {dataset_id?, descriptor_name?} → [runs] | 否 |
| `result.get` | {run_id} → metadata + 摘要（不含大数组） | 否 |
| `analysis.pca` | {run_id, mode?: "structure"\|"atom"} → {job_id}；mode 缺省 structure（每帧一点，原子/配对行均值池化）；atom 模式每个原子/配对行一点并带 frame/atom 索引，超大结果均匀降采样至 ≤20k 点 | 是 |
| `result.get_pca` | {analysis_id} → pca.json 全文（points/explained_variance/x_label/y_label/mode，点数=帧数或原子行数，非大数组） | 否 |
| `result.heatmap` | {run_id, frame_index, max_features?} → {atoms, features, values, atomOffset}；max_features 硬上限 256（§25） | 否 |
| `settings.get` | {key} → {key, value\|null}（settings 表 KV） | 否 |
| `settings.set` | {key, value} → {ok} | 否 |
| `engine.check_update` | {} → {installed, latest, has_update, status: idle\|checking\|up_to_date\|available\|error\|unsupported, error?, restart_required?}；后台线程查 PyPI，完成后再次广播 `engine.update.state` 事件（同结构） | 否（后台线程） |
| `engine.update` | {version?}（缺省用 latest）→ {job_id, target_version}；pip 升级 job，终态后需重启后端生效；frozen 构建报 `ENGINE_UPDATE_UNSUPPORTED` | 是 |
| `job.list` / `job.get` / `job.cancel` | 见 §4 | 否 |

## 6. 错误码全集（16）

```text
DATASET_NOT_FOUND        DATASET_CHANGED         INVALID_DATASET
UNSUPPORTED_FORMAT       UNSUPPORTED_PERIODICITY MDESCRIPTOR_INCOMPATIBLE
DESCRIPTOR_CONFIGURATION_ERROR                   MODEL_NOT_FOUND
OUT_OF_MEMORY            JOB_CANCELLED           RESULT_INCOMPATIBLE
INTERNAL_ERROR           PROTOCOL_VERSION_MISMATCH       JOB_NOT_FOUND
INVALID_PARAMS           ENGINE_UPDATE_UNSUPPORTED
```

（`JOB_CANCEL_UNSUPPORTED` 由 job.cancel 以 `INVALID_PARAMS` 携带 details 表达，不单列。）

错误帧 `message` 为面向开发者的英文；用户友好文案由前端按 code 映射；traceback 只进日志。

## 7. Sidecar 生命周期

- **dev**：Tauri 直接 spawn `.venv\Scripts\python.exe -m mdescriptor_studio_backend`（cwd=backend/，env `MDS_DATA_DIR` 可覆盖数据目录）。
- **release**：spawn PyInstaller 产物 `binaries/backend-x86_64-pc-windows-msvc.exe`（externalBin）。
- Rust 侧：stdout 按行转发给 webview（事件 `backend-message`）；`backend_send(line)` 命令写入 stdin；子进程退出 → 事件 `backend-exit`，前端置后端不可用并提供重启入口。
- 应用退出：关闭 stdin → 等待 ≤ 3s → kill。
