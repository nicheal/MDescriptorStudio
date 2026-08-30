# 03 · Backend Design（mdescriptor_studio_backend）

> 状态：定稿（M1 入口条件）；与代码同步维护
> 依据：设计文档 §40–§46、ADR-8/16/17

## 1. 包结构（与代码一致）

```text
backend/mdescriptor_studio_backend/
├── __main__.py / main.py        # 入口：装配 services、backend.ready、serve_forever
├── config.py                    # %LOCALAPPDATA%\MDescriptorStudio（MDS_DATA_DIR 可覆盖）
├── logging_setup.py             # backend.log 轮转；stdout 只承载协议帧
├── errors.py                    # 15 个错误码 + AppError
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
│   └── analysis_service.py      # PCA：numpy SVD，atom/pair 级按 structure mean-pool + 持久缓存
└── storage/database.py          # SQLite WAL + 写锁 + 迁移(当前 v1)
```

## 2. 数据模型（DDL 见 storage/database.py MIGRATIONS[1]）

`datasets / dataset_statistics / jobs / descriptor_runs / analysis_runs / settings / schema_version`

- `datasets.periodicity`：`{fully_periodic, isolated, mixed, flags}` JSON——ADR-11 预检数据源。
- `descriptor_runs.cache_key = SHA256(fingerprint, name, canonical_params, engine_version, scope, frame_index, dtype)`——§28 缓存键。
- `descriptor_runs.descriptor_version`：记录 `describe_descriptor()` 返回的 per-descriptor 版本（0.2.7 内置描述符均为 `"1"`；见 engine-api-report）。
- `analysis_runs` 按 `descriptor_run_id + analysis_type + params_json(mode)` 查找 PCA 结果；完成且 `pca.json` 存在时直接复用，`QUEUED/RUNNING` 请求复用关联 job。

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

PCA 请求先查 `analysis_runs` 的同 run/mode 完成记录；命中时返回
`{job_id: null, analysis_id, cache: {existing_analysis_id}}`，不重新执行 SVD。

## 5. 指纹与失效

- 注册时 fingerprint 入 `datasets.fingerprint`；`dataset.list/get` 实时重算比对得 `cache_valid`。
- `dataset.statistics`：指纹命中 → 缓存直出；否则自动触发重算 job（ADR-8 最小 JobService）。
- `descriptor.submit` 前置 `refresh_if_changed`：数据集变更 → `DATASET_CHANGED`。

## 6. 开发运行

```bash
.venv\Scripts\python.exe -m mdescriptor_studio_backend   # cwd=backend/，stdin/stdout NDJSON
.venv\Scripts\python.exe -m pytest tests/ -q             # 仓库根
```
