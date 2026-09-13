# 05 · Engine Adapter 设计（mdescriptor_adapter 单点边界）

> 状态：定稿（grilling 共识 2026-08-28）
> 依据：`MDescriptor_GUI_Design.md` §7/§8/§41、`engine-api-report.md`（0.2.8 实测）、ADR-2/ADR-11
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

- 开发与 Release 一律从 PyPI 安装 mdescriptor，`backend/requirements.txt` 以 `mdescriptor>=0.3.2` 约束下限（2026-09-13 起，由 `==0.2.8` 放开：CUDA 插件自 0.3.x 随 wheel 发布，0.2.8 声明 `cuda` 却无插件导致发布版 `DEVICE_UNAVAILABLE`）。**发布构建时装 PyPI 最新版**；开发 `.venv` 已满足下限时 pip 不自动升级，引擎升级由人工执行下述流程；不做 editable 安装；引擎无本地仓库，PyPI 为唯一来源。
- 引擎升级流程（固定四步，缺一不可）：
  1. 必要时抬高 requirements.txt 中的版本下限；
  2. `.venv\Scripts\python.exe scripts\probe_engine.py --out docs/plan/engine-api-report.json` 重跑探测；
  3. diff 新旧 JSON，核对 §5 差异清单是否新增（尤其 parameter type / input capability / asset policy）；
  4. 更新 `engine-api-report.md` 版本头并跑 backend pytest 回归。
- `backend.ready` 事件携带 `mdescriptor_version`，前端与期望 pin 不符时报 `MDESCRIPTOR_INCOMPATIBLE`。

## 3. API 映射表

| GUI 需要 | Adapter 接口 | 引擎调用（0.2.8 实测） |
|---|---|---|
| 启动兼容检查 | `runtime_info()` | `get_runtime_info()` → 版本信息（0.2.7 起 `baseline_version=2`、`descriptor_info_schema_version=3`） |
| Descriptor 列表页 | `list_names()` | `list_descriptors()`（名字 tuple，禁止硬编码） |
| Descriptor 信息面板 / schema 表单 | `schema(name)` | `describe_descriptor(name)`（0.2.7 起为 15 键、`schema_version=3`，0.2.8 保持；每个参数含 `display_name` / `description`，含嵌套 object、input、asset） |
| 构造计算实例 | `build(name, params, device="cpu")` | `DescriptorConfiguration(schema_version=CONFIGURATION_SCHEMA_VERSION, ...)`（`device != "cpu"` 时注入保留键 `execution: {device}`，引擎还原为 `ExecutionOptions`）→ `create_descriptor(cfg)` |
| 提交计算 | `compute(descriptor, batch, control)` | `descriptor.compute(batch, control=control)` |
| 取消 | `cancel(control)` | `control.cancel()` |
| 进度 | `progress(control)` | `control.completed() / control.total()` |
| 数据集帧 → 引擎输入 | `to_structure_batch(frames)` | numbers/positions/cells/pbc/offsets/ids（§11）；energy/forces/virial 不入 batch（§10） |

## 4. 能力探测映射（ADR-11）

| schema 字段 | Adapter 输出 | 消费方 |
|---|---|---|
| `execution.devices` | `devices: list[str]`（0.2.8 起 28/28 为 `["cpu", "cuda"]`；0.2.7 及之前全为 `["cpu"]`） | Descriptor 页 Execution 区设备下拉：选项 = schema 声明列表（唯一声明 cpu 时保持禁用单选）；选择随 `descriptor.submit` 的 `device` 提交，服务端按 schema 校验并计入缓存键；默认 `"cpu"` |
| `execution.cooperative_cancel` | `cancelable: bool` | JobService 决定 Cancel 按钮态（否则显示 Cancel unavailable，§23）；0.2.5 起 28/28 均 true，0.2.8 保持可中断 |
| `execution.num_threads` | `threadable: bool` | 支持时显示线程数输入；CPU 可填 1–64 的整数，留空使用引擎默认；CUDA 禁用。`descriptor.submit.num_threads` 经校验传入引擎 `execution.num_threads`，显式值参与缓存键并记录到结果 metadata |
| `input.periodicity / mixed_periodicity / spin / charge_spin` | `input_caps` | M3 兼容性预检：与 M1 scan 存的 dataset periodicity 汇总比对，不兼容项禁用 + tooltip；提交时兜底 `UNSUPPORTED_PERIODICITY`。0.2.5 起 mixed_periodicity 为逐描述符能力位（22/28 接受混合批次） |
| `asset.policy` | `model_spec` | `none` → 无模型区；`required` → Model picker 两态：内置（空参数，bundled 自动解析）/ 自定义（`model` 参数传本地路径字符串） |

## 5. 错误转换

引擎异常 → GUI 错误码（协议层全集见 `02-IPC_PROTOCOL.md`）：

| 引擎异常 | GUI 错误码 | 用户信息方向 |
|---|---|---|
| `DescriptorConfigError` | `DESCRIPTOR_CONFIGURATION_ERROR` | 参数不合法，指出字段名 |
| `ModelLoadError` | `MODEL_NOT_FOUND` | 模型路径不可用 |
| `DescriptorInputError` | `UNSUPPORTED_PERIODICITY` / `INVALID_DATASET` | 数据集与描述符不兼容；0.2.5 起按异常的 `code` 分流（`unsupported_periodicity` → UNSUPPORTED_PERIODICITY，其余 → INVALID_DATASET） |
| `MDescriptorError`（`code=device_unavailable`） | `DEVICE_UNAVAILABLE` | 请求的设备无运行时（如无 NVIDIA GPU/driver）；schema 声明 cuda 但本机不可用即计算期报此码 |
| `CancelledError` | `JOB_CANCELLED` | 非错误，任务态置 CANCELLED |
| `PackageNotFoundError` | `MDESCRIPTOR_INCOMPATIBLE` | 引擎安装/版本问题 |
| `ClosedDescriptorError` | `INTERNAL_ERROR` | 记日志，通用失败 |
| `MDescriptorError`（基类兜底） | `INTERNAL_ERROR` | traceback 进日志，不进 UI |

`DATASET_NOT_FOUND / DATASET_CHANGED / OUT_OF_MEMORY / RESULT_INCOMPATIBLE / JOB_NOT_FOUND / INVALID_PARAMS / PROTOCOL_VERSION_MISMATCH` 由 services/协议层产生，不经本模块。

## 6. 约束

- **启动预热（保留，0.2.3 实测 / 0.2.8 复核）**：0.2.3 中 `create_descriptor` 会懒加载原生扩展模块，若在存在工作线程/stdin 读取线程时触发该 import，会与 import 机制死锁（构建永久阻塞直到 stdin EOF）；**0.2.5 已修复**并自该版提供 `preload_native()`（0.2.8 保留）。预热仍保留作为纵深防御，并把首次构建开销付在启动期——main() 发出 `backend.ready` 后在后台 warmup 线程调用 `adapter.warmup()`（先显式预加载原生模块，再逐个构建全部 28 个描述符并做一次微计算），使 UI 不必等待预热；期间 `EngineAdapter.build()` 以事件门控等待预热完成（预热线程自身旁路），保证懒加载 import 仍只发生一次且不与用户 job 并发。配套约束：serve_forever 在 Windows 管道 stdin 上使用 PeekNamedPipe 轮询 + os.read 直读（1ms 间隔）而非阻塞 ReadFile——实测阻塞中的 stdin 读取与后台线程导入 sklearn 等数值栈（0.2.x 时含 numba；torch 驻留后的 DLL 加载）并发会死锁 Windows DLL 加载器（scipy.linalg.blas create_module 永久挂起）；warmup 完成后自动切回历史阻塞读取（零延迟、零空闲 CPU）。轮询模式必须绕开 Python 的缓冲 stdin：BufferedReader 会把一次 ReadFile 预取的多行滞留在用户态缓冲，PeekNamedPipe 看到的 OS 队列为空，导致后续帧永久饥饿。main() 在启动预热线程前还会在主线程串行预加载剩余的 DLL 承载包（scipy.spatial、dpdata），因为并发的首次原生导入同样会死锁。控制台 stdin 与非 Windows 平台回退为历史阻塞读取。新增描述符注册路径（如有）不得绕过该预热。
- 只写 canonical 字段（schema 的 `name` 键），不使用历史 Python aliases（§8.2）。
- 参数表单直接使用 schema 参数的 `display_name` 和 `description`；canonical 键名仅用于状态、序列化和提交，不在 GUI 侧维护参数名映射表。
- 大数组不经过本模块进 IPC（Rule 6）：`compute` 返回的 `DescriptorResult` 由 ResultService 直接落盘 values.npy。
- 本模块的任何行为变化都必须先反映在 `engine-api-report.json` 的 diff 里，不允许「代码先行、文档后补」。
