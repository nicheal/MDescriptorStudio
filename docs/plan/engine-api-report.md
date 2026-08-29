# Engine API 探测报告（mdescriptor 0.2.3）

> 日期：2026-08-28
> 探测环境：`D:\codex\MD\.venv`（Python 3.12.9）；mdescriptor 0.2.3（cp312 win_amd64 wheel）+ numpy 2.5.2
> 探测方式：`scripts/probe_engine.py`（只读，不实例化描述符、不加载模型）
> 原始数据：`docs/plan/engine-api-report.json`（升级版本 pin 后重跑脚本 diff 此文件）
> 结论效力：本报告为 GUI 侧 schema 的事实基线（ADR-4、ADR-2）

---

## 1. Runtime Info

`get_runtime_info()` 返回：

```json
{
  "version": "0.2.3",
  "api_version": 1,
  "configuration_schema_version": 1,
  "descriptor_info_schema_version": 1,
  "result_schema_version": 1
}
```

与设计文档 §8.5 契约一致，M0 启动兼容检查直接可用。

## 2. 描述符清单（28 个，`list_descriptors()` 返回名字 tuple）

ACE, ACSF, AtomicComposition, C00PSMLFF, CoulombMatrix, DPA4, DPA4C, EAD,
EwaldSumMatrix, LBispectrum, LMBTR, LodeSphericalExpansion, MBTR, MTP, NEP,
NeighborList, SNAP, SO3, SO4, SOAP, SOAPTurbo, SineMatrix, SoapPowerSpectrum,
SoapRadialSpectrum, SortedDistances, SphericalExpansion, SphericalExpansionByPair,
ValleOganov

GUI 不硬编码此清单（Rule 3），仅作回归比对基准。

## 3. Schema 结构（`describe_descriptor(name)`）

顶层键（13 个）：

```text
schema_version, name, display_name, description, category, level,
backend, capabilities, parameters, execution, input, output, asset
```

- 与设计文档 §8.1 示例相比**更丰富**：多出 `display_name` / `description` / `category` / `capabilities`（list，如 `["cooperative_cancel", "num_threads", "sparse"]`）。
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
| model | 4 | Model picker（§95；0.2.3 无 string 类型参数） |

注意：设计文档 §8.2 列出了 `string` 类型，**0.2.3 实测未使用**；form 生成器按上表 8 种实现，`string` 作为未知类型的兜底渲染（单行文本）。

### 3.2 嵌套 object 参数（重要）

0.2.3 存在 21 处 `type: "object"` 参数，带 `properties` 子 schema（如 ACE 的 `trans`、`D`）。
**动态表单生成器必须支持一层嵌套子表单**（子字段类型同样受限上表），这是设计文档 §8.2 受控字段清单之外的真实需求。

### 3.3 Input Capability（§8.4）

实测 3 种变体：

```json
{"periodicity": ["fully_periodic"],            "mixed_periodicity": false, "spin": false, "charge_spin": false}
{"periodicity": ["isolated", "fully_periodic"],"mixed_periodicity": false, "spin": false, "charge_spin": false}
{"periodicity": ["isolated", "fully_periodic"],"mixed_periodicity": false, "spin": true,  "charge_spin": true}
```

含「仅 fully_periodic」的描述符 → ADR-11 兼容性预检（M1 存周期性汇总，M3 禁用不兼容项）有真实触发场景。

### 3.4 Execution Capability

**0.2.3 全部 28 个描述符 `devices: ["cpu"]`**（无 CUDA 条目）。v0.1 设备选择固定 CPU，UI 不渲染 GPU 选项；`num_threads` / `cooperative_cancel` 均为 true（逐 descriptor 以 schema 为准，不硬编码）。

### 3.5 Asset / Model（§95 相关）

- `policy: "none"`（大多数）：无资源。
- `policy: "required"`（NEP/DPA4 等）：`asset.parameter` 指明参数名（`"model"`），并带 `bundled_resources`（如 `nep89_20250409.txt`）与 `file_extensions`。
- **实测**：`DescriptorConfiguration(schema_version, descriptor="NEP", parameters={})` → `create_descriptor(cfg)` 空参数即实例化成功，**bundled 资源自动解析**；仅自定义模型需按 §8.2 传本地路径字符串。

### 3.6 构造路径（adapter 必须使用）

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
- **错误类型**：`MDescriptorError`（基类）、`DescriptorConfigError`、`DescriptorInputError`、`ModelLoadError`、`ClosedDescriptorError`、`CancelledError`、`PackageNotFoundError`。GUI 错误码映射见 `05-ENGINE_ADAPTER.md`。
- **核心符号全部存在**：`StructureBatch / Descriptor / DescriptorResult / DescriptorRegistry / DescriptorConfiguration / ExecutionOptions / OutputOptions / ComputeControl / describe_descriptor / get_runtime_info / list_descriptors / create_descriptor`。

## 5. 与设计文档 §8 契约的差异清单

| # | 差异 | 影响 |
|---|---|---|
| 1 | schema 多 `display_name / description / category / capabilities` | 信息面板直接展示（§19.2） |
| 2 | `object` 嵌套参数真实存在（21 处） | form 生成器须支持一层嵌套（04 文档硬性要求） |
| 3 | `string` 类型 0.2.3 未使用 | 按未知类型兜底渲染 |
| 4 | devices 全为 `["cpu"]` | v0.1 不渲染 GPU 选项 |
| 5 | asset 多 `bundled_resources / file_extensions`，bundled 自动解析 | Model picker 区分「内置 / 自定义路径」两态 |
| 6 | **无 per-descriptor version 字段** | `descriptor_runs.descriptor_version` 落库用引擎版本（0.2.3）代替，列保留 |
| 7 | **DPA4/DPA4C 的 `backend: "numpy"` 标注与实际执行路径不一致**（2026-08-29 实测）：schema 标 numpy，但 bundled 检查点（DPA4-Air-OMat24-v20260704.pt / DPA4C-Air-OMat24-v20260819.pt）为 graph-native 默认架构，内核实例持有 `_native.Dpa4Calculator` / `Dpa4cCalculator`——**实际计算走 C++**；numpy 仅为非默认图结构/缺 native 符号时的 fallback（内核 docstring 自述 "native C++ core and NumPy fallback"）。GUI 按 schema 展示 `numpy` 是遵循 Rule 5 的正确行为；另注意两者 `cooperative_cancel: false`（界面显示 Cancel unavailable，与 NEP 的 true 不同） | 信息面板如实展示 schema；如需修正标注属引擎侧问题，走上游 issue |
