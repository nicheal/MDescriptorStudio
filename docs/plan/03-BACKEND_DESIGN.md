# 03 · Backend Design（mdescriptor_studio_backend）

> 状态：Analysis v1 已落地；与代码同步维护
> 依据：设计文档 §40–§46、ADR-8/16/17

## 1. 包结构（与代码一致）

```text
backend/mdescriptor_studio_backend/
├── __main__.py / main.py        # 入口：装配 services、backend.ready、serve_forever
├── config.py                    # %LOCALAPPDATA%\MDescriptorStudio（MDS_DATA_DIR 可覆盖）
├── logging_setup.py             # backend.log 轮转；stdout 只承载协议帧
├── errors.py                    # 24 个错误码 + AppError
├── mdescriptor_adapter.py       # 引擎单点边界（05 文档）
├── protocol/
│   ├── frames.py                # NDJSON 帧 encode/parse/版本守卫
│   └── server.py                # stdio 循环 + 4 线程请求池 + 写锁；版本不匹配 os._exit(2)
├── datasets/
│   ├── base.py                  # DatasetFrame / DatasetAdapter / 格式探测 / pbc 汇总
│   ├── deepmd.py                # dpdata 封装（deepmd/npy 语义，全量载入内存；ADR-19）
│   ├── deepmd_symbols.py        # 元素符号 ↔ 原子序数表（extxyz/统计共用）
│   ├── extxyz.py                # 字节偏移帧索引（大文件随机访问），纯手写解析
│   ├── fingerprint.py           # SHA-256(path+文件表+size+mtime+帧数)
│   └── statistics.py            # 流式统计，40-bin 直方图（大数组不过 IPC）
├── services/
│   ├── job_service.py           # 2 线程 job 池；QUEUED→RUNNING→终态；ComputeControl 挂接；进度节流(1%/200ms)
│   ├── dataset_service.py       # 注册(job)/移除/列表/统计缓存/帧序列化(xyz+atom_rows)
│   ├── descriptor_service.py    # registry/describe/submit(参数校验+input 预检+缓存键+compute job+落盘)
│   ├── result_service.py        # run 历史/metadata；load_values 仅供分析
│   ├── analysis_service.py      # 统一 Analysis API、JobManager、artifact/cache、导出
│   └── analysis/
│       ├── engine.py             # CPU-first PCA/UMAP/t-SNE/cluster/outlier/sampling/quality
│       └── models.py             # AnalysisResult / ArtifactManifest 序列化契约
└── storage/database.py          # SQLite WAL + 写锁 + 迁移(当前 v3)
```

## 2. 数据模型（DDL 见 storage/database.py MIGRATIONS[3]）

`datasets / dataset_statistics / jobs / descriptor_runs / analysis_runs / settings / schema_version`

- `datasets.periodicity`：`{fully_periodic, isolated, mixed, flags}` JSON——ADR-11 预检数据源。
- `descriptor_runs.cache_key = SHA256(fingerprint, name, canonical_params, engine_version, scope, frame_index, dtype)`——§28 缓存键。
- `descriptor_runs.descriptor_version`：记录 `describe_descriptor()` 返回的 per-descriptor 版本（0.2.7 内置描述符均为 `"1"`；见 engine-api-report）。
- `analysis_runs` 保存 `input_run_ids_json`、`dataset_ids_json`、规范化 `params_json`、`cache_key`、`schema_version`、`algorithm_version`、`preprocessing_json`、`warnings_json`、`artifact_manifest_json`、`preview_json` 与 `stale_reason`。所有通用分析按输入 Run + 参数 + 算法版本查缓存；legacy PCA 仍兼容旧 `pca.json`。
- Analysis 结果目录采用 `analysis/<analysis_id>/`，包含 named `.npy`、`metadata.json` 和已完成 `manifest.json`；写入 `.tmp-*` 后使用原子目录替换。

## 3. 线程模型（ADR-17）

- 请求池 4 线程（protocol/server.py），job 池 2 线程（job_service）。
- SQLite：单连接 + `RLock` 串行写，WAL 模式。
- 取消：`JobContext.cancel()` 置位 + `control.cancel()`（cooperative）；runner 在帧间调 `check_cancelled()`。
- 结果落盘：`results/run_<id>/values.npy + row_offsets.npy + metadata.json`（§26/§27 契约）。

## 4. 数据流（提交计算）

```text
descriptor.submit
  → 校验（参数类型 8 种 + 嵌套 object 一层 + required；input capability vs dataset.periodicity）
  → cache_key 命中且未 force → {job_id: null, cache:{existing_run_id}}
  → descriptor_runs 入库 + job 入队
  → job: build(engine) → frames(scope) → StructureBatch(offsets 哨兵) → ComputeControl
        → compute → values.npy/metadata.json → run COMPLETED
  → job.finished 事件 {run_id, shape, dtype, level, feature_count}
```

Analysis 请求先按 `analysis_type + input_run_ids + 规范化 parameters + algorithm_version` 查 cache key；命中时返回
`{job_id: null, analysis_id, cache: {existing_analysis_id, cache_key}}`，不重新执行计算。PCA 旧入口的缓存响应保持同一异步外形。

## 5. 指纹与失效

- 注册时 fingerprint 入 `datasets.fingerprint`；`dataset.list/get` 实时重算比对得 `cache_valid`。
- `dataset.statistics`：指纹命中 → 缓存直出；否则自动触发重算 job（ADR-8 最小 JobService）。
- `descriptor.submit` 前置 `refresh_if_changed`：数据集变更 → `DATASET_CHANGED`。
- 数据源 fingerprint 变化时不删除历史 Descriptor/Analysis；已完成 Run 标记 `STALE` 并记录原因。`result.get` 可读取 stale 结果用于审计，但新 Analysis 输入必须是当前 fingerprint 对应的 `COMPLETED` Run。

## 6. 开发运行

## 7. Analysis API 与资源边界

支持的 IPC 方法包括：

analysis.list/get/delete/preview/chunk、analysis.pca、analysis.umap、
analysis.tsne、analysis.neighbors、analysis.similarity、analysis.cluster、
analysis.outlier、analysis.fps、analysis.sampling、analysis.coverage、
analysis.compare、analysis.feature_variance、analysis.feature_correlation、
analysis.effective_dimension、analysis.trajectory、analysis.drift、
analysis.sensitivity、analysis.export。

PCA/UMAP/t-SNE 结果为 preview points；cluster/outlier/coverage 结果为
preview rows；large arrays 通过 chunk 访问，preview 与单次 chunk 均受
20,000 行硬上限。计算中的自有循环调用 ctx.check_cancelled()，JobService
默认并发为 2。距离/coverage 使用分块 nearest-neighbour，避免默认构造完整
N×N 矩阵。Analysis 依赖由 backend/requirements.txt 和 backend.spec 一起发布。

```bash
.venv\Scripts\python.exe -m mdescriptor_studio_backend   # cwd=backend/，stdin/stdout NDJSON
.venv\Scripts\python.exe -m pytest tests/ -q             # 仓库根
```
