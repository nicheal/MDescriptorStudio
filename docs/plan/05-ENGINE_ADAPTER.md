# 05 · Engine Adapter 设计（mdescriptor_adapter 单点边界）

> 状态：定稿（grilling 共识 2026-08-28）
> 依据：`MDescriptor_GUI_Design.md` §7/§8/§41、`engine-api-report.md`（0.2.3 实测）、ADR-2/ADR-11
> 定位：GUI 全项目**唯一**允许 `import mdescriptor` 的模块；其余代码只依赖本模块的纯 Python 接口

---

## 1. 模块位置与职责

```text
backend/
└── mdescriptor_studio_backend/
    └── mdescriptor_adapter.py    # 单点边界
```

职责（设计文档 §41）：

```text
runtime info 读取          → get_runtime_info()
list descriptors          → list_descriptors()
describe descriptor       → describe_descriptor(name)
build descriptor          → DescriptorConfiguration + create_descriptor()
compute（含进度/取消）      → descriptor.compute(batch, control=...)
convert errors            → 引擎异常 → GUI 错误码
```

本模块**无状态**：不持有 SQLite、不做 IPC、不感知 Job；线程安全由「每次 compute 传入独立 ComputeControl」保证。

## 2. 版本策略（ADR-2）

- 开发与 Release 一律 pin `mdescriptor==0.2.3`（不做 editable 安装；引擎无本地仓库，PyPI 为唯一来源）。
- 升级流程（固定四步，缺一不可）：
  1. 改 pin 版本号；
  2. `.venv\Scripts\python.exe scripts\probe_engine.py --out docs/plan/engine-api-report.json` 重跑探测；
  3. diff 新旧 JSON，核对 §5 差异清单是否新增（尤其 parameter type / input capability / asset policy）；
  4. 更新 `engine-api-report.md` 版本头并跑 backend pytest 回归。
- `backend.ready` 事件携带 `mdescriptor_version`，前端与期望 pin 不符时报 `MDESCRIPTOR_INCOMPATIBLE`。

## 3. API 映射表

| GUI 需要 | Adapter 接口 | 引擎调用（0.2.3 实测） |
|---|---|---|
| 启动兼容检查 | `runtime_info()` | `get_runtime_info()` → 五元组版本 |
| Descriptor 列表页 | `list_names()` | `list_descriptors()`（名字 tuple，禁止硬编码） |
| Descriptor 信息面板 / schema 表单 | `schema(name)` | `describe_descriptor(name)`（13 键，含嵌套 object、input、asset） |
| 构造计算实例 | `build(name, params)` | `DescriptorConfiguration(schema_version=CONFIGURATION_SCHEMA_VERSION, ...)` → `create_descriptor(cfg)` |
| 提交计算 | `compute(descriptor, batch, control)` | `descriptor.compute(batch, control=control)` |
| 取消 | `cancel(control)` | `control.cancel()` |
| 进度 | `progress(control)` | `control.completed() / control.total()` |
| 数据集帧 → 引擎输入 | `to_structure_batch(frames)` | numbers/positions/cells/pbc/offsets/ids（§11）；energy/forces/virial 不入 batch（§10） |

## 4. 能力探测映射（ADR-11）

| schema 字段 | Adapter 输出 | 消费方 |
|---|---|---|
| `execution.devices` | `devices: list[str]`（0.2.3 全为 `["cpu"]`） | Descriptor 页 Execution 区；无 GPU 时不渲染设备选择 |
| `execution.cooperative_cancel` | `cancelable: bool` | JobService 决定 Cancel 按钮态（否则显示 Cancel unavailable，§23） |
| `execution.num_threads` | `threadable: bool` | 线程数输入是否出现 |
| `input.periodicity / spin / charge_spin` | `input_caps` | M3 兼容性预检：与 M1 scan 存的 dataset periodicity 汇总比对，不兼容项禁用 + tooltip；提交时兜底 `UNSUPPORTED_PERIODICITY` |
| `asset.policy` | `model_spec` | `none` → 无模型区；`required` → Model picker 两态：内置（空参数，bundled 自动解析）/ 自定义（`model` 参数传本地路径字符串） |

## 5. 错误转换

引擎异常 → GUI 错误码（协议层全集见 `02-IPC_PROTOCOL.md`）：

| 引擎异常 | GUI 错误码 | 用户信息方向 |
|---|---|---|
| `DescriptorConfigError` | `DESCRIPTOR_CONFIGURATION_ERROR` | 参数不合法，指出字段名 |
| `ModelLoadError` | `MODEL_NOT_FOUND` | 模型路径不可用 |
| `DescriptorInputError` | `UNSUPPORTED_PERIODICITY` / `INVALID_DATASET` | 数据集与描述符不兼容 |
| `CancelledError` | `JOB_CANCELLED` | 非错误，任务态置 CANCELLED |
| `PackageNotFoundError` | `MDESCRIPTOR_INCOMPATIBLE` | 引擎安装/版本问题 |
| `ClosedDescriptorError` | `INTERNAL_ERROR` | 记日志，通用失败 |
| `MDescriptorError`（基类兜底） | `INTERNAL_ERROR` | traceback 进日志，不进 UI |

`DATASET_NOT_FOUND / DATASET_CHANGED / OUT_OF_MEMORY / RESULT_INCOMPATIBLE / JOB_NOT_FOUND / INVALID_PARAMS / PROTOCOL_VERSION_MISMATCH` 由 services/协议层产生，不经本模块。

## 6. 约束

- **启动预热（关键，0.2.3 实测）**：`create_descriptor` 会懒加载原生扩展模块；若在存在工作线程/stdin 读取线程时触发该 import，会与 import 机制死锁（构建永久阻塞直到 stdin EOF）。因此 main() 在启动任何线程之前，于主线程调用 `adapter.warmup()`——逐个构建全部 28 个描述符并做一次微计算，`backend.ready` 在预热完成后才发出。新增描述符注册路径（如有）不得绕过该预热。
- 只写 canonical 字段（schema 的 `name` 键），不使用历史 Python aliases（§8.2）。
- 大数组不经过本模块进 IPC（Rule 6）：`compute` 返回的 `DescriptorResult` 由 ResultService 直接落盘 values.npy。
- 本模块的任何行为变化都必须先反映在 `engine-api-report.json` 的 diff 里，不允许「代码先行、文档后补」。
