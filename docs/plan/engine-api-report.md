# Engine API 探测报告（mdescriptor 0.3.3）

> 日期：2026-09-13（0.2.8 → 0.3.3 升级后重测；历史 0.2.3/0.2.5/0.2.6/0.2.7/0.2.8 报告见 git 历史）
> 探测环境：`D:\codex\MD\.venv`（Python 3.12.9）；mdescriptor 0.3.3（cp312 win_amd64 wheel，发布构建时 PyPI 最新）+ numpy 2.5.2
> 探测方式：`scripts/probe_engine.py`（只读，不实例化描述符、不加载模型）
> 原始数据：`docs/plan/engine-api-report.json`（引擎版本变化后重跑脚本 diff 此文件）
> 结论效力：本报告为 GUI 侧 schema 的事实基线（ADR-4、ADR-2）
> 升级复核：逐条问题复核结论见 `engine-known-issues.md`；0.2.8→0.3.3 schema diff 要点：
> - **probe JSON 逐键 diff：仅 `engine_version` / `runtime_info.version` 两处版本号变化，schema 零漂移**（28 描述符、参数类型统计、输入能力矩阵、asset policy、错误类型、符号表全部一致）
> - **CUDA 插件自 0.3.x 随 wheel 发布**（`mdescriptor/_cuda*.pyd` + `cudart64_12.dll`；0.2.8 仅在 schema 声明 `cuda` 而无插件，为发布版 `DEVICE_UNAVAILABLE` 的根因）；CUDA 计算路径已在 RTX 2080 SUPER 实测通过（引擎直调 + 冻结 sidecar 端到端）
> - PyInstaller `collect_all("mdescriptor")` 已验证将 `_cuda.pyd` 与 `cudart64_12.dll` 打入 onefile sidecar
> - 版本策略（ADR-2）：`requirements.txt` 以 `mdescriptor>=0.3.2` 约束下限，发布构建装 PyPI 最新版；**每次发版前以本仓库 `.venv` 实装最新版重跑本探测并跑回归**
>
> 历史 0.2.7→0.2.8 diff 要点：
> - **唯一 schema 变化：28 个描述符的 `execution.devices` 全部由 `["cpu"]` 扩为 `["cpu", "cuda"]`**（上游 GPU 路径条目落地声明层）
> - `get_runtime_info().version` 随之升为 `"0.2.8"`；`baseline_version="2"`、`descriptor_info_schema_version=3` 等版本位不变
> - 参数类型统计（8 类 170 个）、输入能力矩阵、asset policy、错误类型、符号表与 0.2.7 完全一致
>
> 历史 0.2.6→0.2.7 diff 要点：
> - 每描述符 `schema_version` 2→3，`get_runtime_info().baseline_version` 升为 `"2"`，`descriptor_info_schema_version` 2→3
> - 28 个内置描述符的 170 个参数全部新增 GUI-facing `display_name` 与 `description`，包括 ACE `trans`/`D` 的嵌套属性
> - 新增 `list_descriptors(detailed=True)`、`StructureBatch.from_frames()` 和 `UnsupportedPeriodicityError`；默认 `list_descriptors()` 仍返回名字 tuple
> - `mdescriptor.gui_baseline()` 提供版本化 GUI 契约文本；`mdescriptor.preload_native()` 继续作为 Windows 单线程启动期预加载入口
> - 其余参数类型统计、输入能力、28 个描述符名单与 0.2.6 一致

---

## 1. Runtime Info

`get_runtime_info()` 返回：

```json
{
  "version": "0.2.8",
  "api_version": 1,
  "baseline_version": "2",
  "configuration_schema_version": 1,
  "descriptor_info_schema_version": 3,
  "result_schema_version": 1
}
```

与引擎 `gui-adaptation-baseline()` 契约一致，M0 启动兼容检查直接可用。

## 2. 描述符清单（28 个，`list_descriptors()` 返回名字 tuple）

ACE, ACSF, AtomicComposition, C00PSMLFF, CoulombMatrix, DPA4, DPA4C, EAD,
EwaldSumMatrix, LBispectrum, LMBTR, LodeSphericalExpansion, MBTR, MTP, NEP,
NeighborList, SNAP, SO3, SO4, SOAP, SOAPTurbo, SineMatrix, SoapPowerSpectrum,
SoapRadialSpectrum, SortedDistances, SphericalExpansion, SphericalExpansionByPair,
ValleOganov

GUI 不硬编码此清单（Rule 3），仅作回归比对基准。

## 3. Schema 结构（`describe_descriptor(name)`）

顶层键（15 个）：

```text
schema_version, name, display_name, description, category, level,
backend, execution_engine, descriptor_version, capabilities,
parameters, execution, input, output, asset
```

- 与设计文档 §8.1 示例相比**更丰富**：多出 `display_name` / `description` / `category` / `capabilities`（list，如 `["cooperative_cancel", "num_threads", "sparse"]`）；0.2.5 加入 `execution_engine`（实际执行内核，全 `"cpp"`）与 `descriptor_version`（结果溯源用，当前全 `"1"`）；0.2.7 将 schema envelope 升为 3。
- `capabilities` 实测取值全集：`charge_spin`、`cooperative_cancel`、`model`、`num_threads`、`sparse`、`spin`。

### 3.1 Parameter 类型全集（form 生成器必须覆盖）

| type | 出现次数 | form 控件（对应 04 文档） |
|---|---|---|
| number | 48 | InputNumber（带 unit suffix，§94） |
| integer | 34 | InputNumber（int） |
| boolean | 22 | Checkbox / Switch |
| species | 16 | 元素 MultiSelect |
| object | 21 | 嵌套子表单（见 3.2） |
| enum | 14 | Select |
| array | 11 | 数字列表输入（ Tags/动态行） |
| model | 4 | Model picker（§95；0.2.8 仍无 string 类型参数） |

注意：设计文档 §8.2 列出了 `string` 类型，**0.2.8 实测仍未使用**；form 生成器按上表 8 种实现，`string` 作为兼容性兜底渲染（单行文本）。

### 3.2 Parameter presentation metadata

0.2.8 的每一个内置参数（170/170）都提供：

```json
{
  "display_name": "Maximum radial order",
  "description": "Number of radial basis functions retained in the expansion."
}
```

GUI 必须用 `display_name` 作为字段标题、用 `description` 作为说明提示，但序列化和提交仍使用 `parameters` 下的 canonical 键名。嵌套 object 属性遵循同一规则；GUI 不应再维护按参数名匹配的显示名/描述硬编码表。

### 3.3 嵌套 object 参数（重要）

0.2.8 存在 21 处 `type: "object"` 参数，其中 10 处带 `properties` 子 schema（如 ACE 的 `trans`、`D`），其余对象保留为 JSON-safe 的自由对象默认值。
**动态表单生成器必须支持一层嵌套子表单**（子字段类型同样受限上表），这是设计文档 §8.2 受控字段清单之外的真实需求。

### 3.4 Input Capability（§8.4）

实测 3 种变体（0.2.8：`mixed_periodicity` 为能力位——true 表示接受同一批次内混合孤立/周期结构）：

```json
{"periodicity": ["isolated", "fully_periodic"], "mixed_periodicity": true,  "spin": false, "charge_spin": false}
{"periodicity": ["fully_periodic"],              "mixed_periodicity": false, "spin": false, "charge_spin": false}
{"periodicity": ["isolated", "fully_periodic"], "mixed_periodicity": true,  "spin": true,  "charge_spin": true}
```

- 第一、三类合计 22/28（mixed_periodicity=true）；第二类为 6 个仅周期描述符（EwaldSumMatrix、LMBTR、LodeSphericalExpansion、MBTR、SineMatrix、ValleOganov）；第三类为 DPA4/DPA4C。
- **0.2.3→0.2.5 行为变化**：mixed_periodicity 从整体拒绝变为逐描述符能力位；LodeSphericalExpansion 的 periodicity 由 `["isolated", "fully_periodic"]` 收窄为 `["fully_periodic"]`；0.2.8 保持该能力矩阵。
- 含「仅 fully_periodic」的描述符 → ADR-11 兼容性预检（M1 存周期性汇总，M3 禁用不兼容项）有真实触发场景。

### 3.5 Execution Capability

2026-09-07 更新：CPU 线程输入已开放；提交字段 `num_threads` 支持 1–64 整数，留空沿用引擎默认。后端校验能力及设备并传到 `execution.num_threads`，显式值参与缓存键、记录到结果 metadata。以下历史描述中的线程限制已解除。

**0.2.8 起 28 个描述符均声明 `devices: ["cpu", "cuda"]`**（0.2.7 及之前为 `["cpu"]`，无 CUDA 条目）。Descriptor 页 Execution 区设备下拉按 schema 声明列表渲染；选择经 `descriptor.submit` 的 `device` 提交，adapter 在 `device != "cpu"` 时以保留键 `execution: {"device": ...}` 注入配置参数（引擎还原为 `ExecutionOptions`），并计入缓存键；无效设备名在建构期被引擎拒绝（`DescriptorConfigError`，`code=invalid_device`），schema 未声明的设备在 submit 时以 `INVALID_PARAMS` 拒绝；声明了但本机无运行时（无 NVIDIA GPU/driver）在计算期报 `DEVICE_UNAVAILABLE`（引擎 `code=device_unavailable`）。默认 `"cpu"`；`num_threads` 仍用引擎默认。`num_threads` / `cooperative_cancel` 逐 descriptor 以 schema 为准；DPA4/DPA4C 的 `cooperative_cancel` 自 0.2.5 起 true。

### 3.6 Asset / Model（§95 相关）

- `policy: "none"`（大多数）：无资源。
- `policy: "required"`（NEP/DPA4 等）：`asset.parameter` 指明参数名（`"model"`），并带 `bundled_resources`（如 `nep89_20250409.txt`）与 `file_extensions`。
- **实测**：`DescriptorConfiguration(schema_version, descriptor="NEP", parameters={})` → `create_descriptor(cfg)` 空参数即实例化成功，**bundled 资源自动解析**；仅自定义模型需按 §8.2 传本地路径字符串。

### 3.7 构造路径（adapter 必须使用）

```python
cfg = md.DescriptorConfiguration(
    schema_version=md.CONFIGURATION_SCHEMA_VERSION,
    descriptor=name,
    parameters={...},   # 严格按 schema 的 canonical 字段
)
descriptor = md.create_descriptor(cfg)
```

禁止 `inspect.signature` 反射（Rule 5）。

## 4. 其他 API 面

- **ComputeControl** 方法：`cancel / cancelled / completed / mark_completed / reset / total`——进度（completed/total）与协作取消（cancel）契约齐全（§23）。
- **错误类型**：`MDescriptorError`（基类）、`DescriptorConfigError`、`DescriptorInputError`、`UnsupportedPeriodicityError`、`ModelLoadError`、`ClosedDescriptorError`、`CancelledError`、`PackageNotFoundError`。GUI 错误码映射见 `05-ENGINE_ADAPTER.md`。
- **核心符号全部存在**：`StructureBatch / Descriptor / DescriptorResult / DescriptorRegistry / DescriptorConfiguration / ExecutionOptions / OutputOptions / ComputeControl / describe_descriptor / get_runtime_info / list_descriptors / create_descriptor`。

## 5. 与设计文档 §8 契约的差异清单

| # | 差异 | 影响 |
|---|---|---|
| 1 | schema 多 `display_name / description / category / capabilities`，参数也提供 `display_name / description` | 信息面板与参数表单直接展示（§19.2） |
| 2 | `object` 嵌套参数真实存在（21 处） | form 生成器须支持一层嵌套（04 文档硬性要求） |
| 3 | `string` 类型 0.2.8 未使用 | 按兼容性兜底渲染 |
| 4 | ~~devices 全为 `["cpu"]`~~ **0.2.8 已声明 CUDA**：28/28 `devices: ["cpu","cuda"]` | Descriptor 页按 schema 渲染设备下拉；`device` 计入缓存键与结果 metadata；GPU 验收需 CUDA 硬件（开发机未实测，见 05 文档） |
| 5 | asset 多 `bundled_resources / file_extensions`，bundled 自动解析 | Model picker 区分「内置 / 自定义路径」两态 |
| 6 | ~~无 per-descriptor version 字段~~ **0.2.5 已修复**：新增 `descriptor_version`，`descriptor_runs.descriptor_version` 已接线记录；0.2.8 保持 | 结果溯源可用真实版本 |
| 7 | ~~DPA4/DPA4C 的 `backend: "numpy"` 标注与实际执行路径不一致~~ **0.2.5 已修复**：新增 `execution_engine`（全 `"cpp"`）承载真实执行路径；`backend` 保留适配器族语义（DPA4/DPA4C 仍 `numpy`）。0.2.8 保持 | 信息面板展示 `backend` + `execution_engine` 两字段 |
| 8 | **LodeSphericalExpansion 输入能力收窄**（0.2.3 `["isolated","fully_periodic"]` → 0.2.5 `["fully_periodic"]`），mixed_periodicity 亦为能力位化；0.2.8 保持 | 周期性预检按 schema 动态工作，无需改码；仅升级说明需记录 |
