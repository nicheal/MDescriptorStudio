# mdescriptor 已知问题清单（基于 0.2.3 实测）

> 日期：2026-08-29；环境：Windows x64，cp312 wheel，项目 `.venv`
> 性质：MDescriptor Studio 开发过程中的实测发现，可直接作为上游 issue 素材（github.com/nicheal/MDescriptor）
> 注：~~PyPI 已出现 0.2.4，以下问题是否仍存在需在升级后按本清单逐条复核~~ **已于 2026-08-29 升级至 0.2.5 并逐条复核**（方法：`scripts/verify_known_issues.py` + `scripts/probe_engine.py` diff；pytest 24 项全绿）
> 仍需上游修改的项已整理为可开工清单：**`upstream-issues.md`**（5 项：wheel 缺契约文档、GPU 路径、`from_frames`、`list_descriptors` 返回类型、可选错误子类）

## 0.2.5 复核总结

| # | 问题 | 0.2.5 状态 |
|---|---|---|
| 1 | 懒加载 import 死锁 | **已修复**（stdin 阻塞线程存活时 ACE 构建 0.02s 完成，原 15s+ 挂起） |
| 2 | backend 标注与执行路径不一致 | **已修复**（新增 `execution_engine` 字段，按建议区分了两个字段） |
| 3 | DPA4/DPA4C 协作取消 | **已修复**（schema 置 true + 实测 4×216 原子计算中途 cancel 抛 `CancelledError`） |
| 4 | 无 per-descriptor 版本号 | **已修复**（新增 `descriptor_version`，28/28 均有） |
| 5 | 周期性校验错误信息不统一 | **已修复（完整）**——统一文案 *"X requires fully_periodic input"* + 结构化错误 payload（`code="unsupported_periodicity"`、`path=["input","periodicity"]`、`details={provided, supported}`）；LodeSphericalExpansion 能力校验先于晶胞检查 |
| 6 | 混合周期性被整体拒绝 | **已修复**（22/28 `mixed_periodicity: true`；SOAP/ACE/SortedDistances 混合批次实测计算成功） |
| 7 | 正式基线文档缺失 | **基本解决**——文档存在于引擎仓库 `docs/gui-adaptation-baseline.md`（2026-08-29 版，内容与 0.2.5 契约逐条吻合）；仅剩"未随 PyPI wheel 发布"这一打包问题 |
| 8 | schema 契约小偏差 | **不存在**——以 baseline 文档为准：受控类型共 9 种（含 `string`，允许声明未使用），object 嵌套 `properties` 与 array `items` 均已入契约；此前偏差是相对 GUI 设计文档 §8.2 转述而言 |
| 9 | 全部 devices: ["cpu"] | **仍存在**（0.2.5 无 CUDA 条目） |
| 10 | API 人体工学 | **部分存在**——无 `from_frames`、`list_descriptors` 仍返回 tuple；但"错误细分"已通过结构化 `code` 落地（无独立异常类，映射按 code 而非类型） |

另有一处**行为收紧**需注意：LodeSphericalExpansion 的输入能力从 0.2.3 的 `["isolated", "fully_periodic"]` 收窄为 0.2.5 的 `["fully_periodic"]`（仅周期）。Studio 的周期性预检按 schema 动态工作，无需代码改动，但升级说明里值得记录。

## 高优先级

### 1. 懒加载 import 死锁（严重）
`create_descriptor` 内部懒加载原生扩展模块；当进程内存在**阻塞读取 stdin 的线程**时，构建永久挂起，直到 stdin EOF 才放行。最小复现：工作线程执行 `create_descriptor("ACE", …)` + 另一线程 `for line in sys.stdin`（管道不关闭）→ 构建阻塞 15s+（= EOF 时刻）。直接后果：任何嵌入宿主进程的用法（GUI sidecar、notebook 管道）都必须在单线程阶段预热全部描述符。Studio 侧已用启动预热规避（05 文档 §6）。建议：引擎导入期预解析全部原生模块，或定位 import 链上的阻塞点。

> **0.2.5 复核：已修复。** 子进程复现（工作线程 `sys.stdin.buffer.read()` 阻塞 + 主线程 `create_descriptor("ACE")` + 微计算）：stdin 保持打开的情况下 0.02s 完成构建（0.2.3 下挂起至 EOF）。修复机制：import 期预加载原生二进制，并新增公开 API `mdescriptor.preload_native()`（baseline 文档载明 Windows 宿主应在单线程启动期 import 或显式调用）。Studio 的启动预热保留为纵深防御 + 预热开销。

### 2. `backend` 标注与实际执行路径不一致
`describe_descriptor("DPA4"/"DPA4C")["backend"] == "numpy"`，但内核自述 "native C++ core and NumPy fallback"，且实测 bundled 检查点实例化后持有 `_native.Dpa4Calculator` / `Dpa4cCalculator` —— **实际计算走 C++**。`backend` 字段当前语义是"适配器实现族"而非执行路径，会误导消费者（GUI 按 Rule 5 如实显示 numpy）。建议：区分 `implementation`（适配器）与 `execution_engine`（实际内核）两个字段，或 backend 取值反映真实路径。

> **0.2.5 复核：已修复（按建议方案）。** schema 新增顶层 `execution_engine` 字段，28/28 均为 `"cpp"`；`backend` 仍为适配器族语义（DPA4/DPA4C 仍标 `numpy`，其余 26 个标 `cpp`）。两个字段并存后语义不再误导；GUI 信息面板可如实展示执行路径。

### 3. DPA4/DPA4C 不支持协作取消
`execution.cooperative_cancel == false`（对比 NEP 为 true）。两者恰是最重的模型描述符，整数据集计算可能数十分钟且**无法中断**，只能等其自然结束。建议实现 ComputeControl 检查点（模型按帧循环处天然可插）。

> **0.2.5 复核：已修复。** schema `execution.cooperative_cancel` 置 `true` 且 `capabilities` 含 `cooperative_cancel`；功能实测：DPA4 在 4×216 原子 Si 批次计算中 1.5s 时 `control.cancel()` → 立即抛 `CancelledError`（`control.cancelled()` 为 true）。

## 中优先级

### 4. 无 per-descriptor 版本号
`list_descriptors()` / `describe_descriptor()` 均不含版本信息；`get_runtime_info` 只有引擎整体版本。下游无法做 per-descriptor 的结果溯源/缓存失效区分（Studio 的 `descriptor_runs.descriptor_version` 只能记引擎版本）。

> **0.2.5 复核：已修复。** `describe_descriptor()` 新增 `descriptor_version`（当前 28/28 均为 `"1"`）；per-descriptor `schema_version` 亦升至 2，`get_runtime_info().descriptor_info_schema_version` 随之 1→2。Studio 侧已接线：`descriptor_runs` 与结果 metadata 现记录 schema 的 `descriptor_version`。

### 5. 周期性校验的错误信息不统一且含糊
- 5 个仅周期描述符（EwaldSumMatrix、LMBTR、MBTR、SineMatrix、ValleOganov）在 isolated 输入下报 *"X does not support input field 'periodicity'"*——措辞像调用方传错参数名，实际含义是"仅支持 fully_periodic"；
- **LodeSphericalExpansion** 同场景报 *"cell matrix is singular"*——输入能力校验发生在晶胞检查之后，错误归因误导。
建议：统一为 `UNSUPPORTED_PERIODICITY` 语义（"requires fully_periodic"），且 capability 校验先于数值检查。

> **0.2.5 复核：已修复（完整，超出原建议）。** 文案统一之外，异常还携带结构化 payload：`DescriptorInputError.code == "unsupported_periodicity"`、`path == ("input", "periodicity")`、`details == {"provided": "isolated", "supported": ["fully_periodic"]}`（`default_code == "invalid_input"` 承载其余输入错误语义）。Studio 侧 `_convert` 已改为按 `code` 分流 `UNSUPPORTED_PERIODICITY` / `INVALID_DATASET`，不再靠异常类型一把抓。

### 6. 混合周期性（mixed periodicity）被整体拒绝
`StructureBatch` 文档明言 "Mixed periodicity is intentionally rejected"——同一批次内部分周期结构（表面+分子、slab+vacuum）直接报错，无逐结构路由。对实际训练集是硬限制；GUI 已用预检规避（禁用不兼容描述符）。建议：至少提供 per-structure 分批计算的上层封装。

> **0.2.5 复核：已修复（能力面）。** 22/28 描述符 `input.mixed_periodicity` 置 `true`，实测 SOAP/ACE/SortedDistances 在「孤立 H2 + 周期 Si2」混合批次上直接 compute 成功。仍拒绝混合的只剩 6 个仅周期描述符（EwaldSumMatrix、LMBTR、LodeSphericalExpansion、MBTR、SineMatrix、ValleOganov）——这是能力差异而非整体硬限制。注意 LodeSphericalExpansion 由 0.2.3 的「支持 isolated」**收窄**为仅 fully_periodic。

### 7. 正式基线文档缺失
设计文档头部引用的 `gui-adaptation-baseline.md`（"基线定义唯一的 JSON 字段、版本、模型资源和执行契约"）既不在 PyPI 发布物也不在仓库 docs 中。schema 受控字段清单（§8.2）与实际存在出入（见 #8），契约的唯一权威来源悬空。

> **0.2.5 复核：基本解决。** 基线文档存在于引擎源码仓库：`MDescriptor/docs/gui-adaptation-baseline.md`（2026-08-29 版，WSL checkout / github.com/nicheal/MDescriptor）。内容为版本化契约，与 0.2.5 实测逐条吻合：15 个顶层字段、`schema_version=2`、`descriptor_version="1"`、`backend` 与 `execution_engine` 的语义区分、输入策略（含 mixed_periodicity 与结构化错误 payload）、Windows 单线程启动 import 指引。**剩余缺口收窄为打包问题**：该文档仍未随 PyPI wheel 发布，已安装环境不可见（上游 issue 价值仍在，但严重度从"契约悬空"降为"发布物缺附件"）。

## 低优先级

### 8. schema 契约与实际的小偏差
- §8.2 受控类型清单含 `string`，0.2.3 实测 0 处使用（8 种类型：species/integer/number/boolean/enum/array/object/model）；
- `object` 参数（21 处，如 ACE 的 `trans`/`D`）与嵌套 `properties` 未在 §8.1 示例/§8.2 清单中定义——但这是真实且有用的能力，建议补进契约而非删能力。

> **0.2.5 复核：不存在（以权威契约为准）。** `gui-adaptation-baseline.md` 明确：受控类型共 9 种——`integer, number, boolean, string, enum, species, model, array, object`；object 可含嵌套 `properties`、array 可含 `items`。即 `string` 是"声明但 0 处使用"的受控类型（合法），object 嵌套是契约内能力。0.2.5 实测类型统计与 0.2.3 一致（`string` 0 处、`object` 21 处）——实现与契约对齐，此前的"偏差"是相对 GUI 设计文档 §8.2 转述而言，非引擎问题。

### 9. 0.2.3 全部 `devices: ["cpu"]`
无任何 GPU 条目。§8.3 的 devices 能力位设计正确，但当前 wheel 未暴露 CUDA 路径（对大模型描述符 DPA4 影响最大）。

> **0.2.5 复核：仍存在。** 28/28 仍 `devices: ["cpu"]`。

### 10. API 人体工学
- `StructureBatch.offsets` 要求 n+1 哨兵（累积式），无 `from_frames()` 类辅助构造器，易错（Studio 已封装）；
- `list_descriptors()` 返回 `tuple`（而非常见 list/record 数组）；
- `DescriptorInputError` 同时承担"周期性不兼容"与"数据集无效"两类语义，细分有利上游映射错误码。

> **0.2.5 复核：部分存在。** "错误细分"一项已解决——异常新增结构化 `code`（`unsupported_periodicity` / `invalid_input` 等）、`path`、`details`（见 #5），上游可按 code 映射而无须独立异常类；Studio `_convert` 已按此实现。仍缺：`StructureBatch.from_frames()` 辅助构造器、`list_descriptors()` 仍返回 `tuple`。

### 11. 计算期间无增量进度回调（`ComputeControl.completed()` 恒为 0）
`ComputeControl` 公开 `completed() / total()`（05 文档 §3 据此设计进度映射），但 0.2.5 内核不增量更新计数器：DPA4C 计算 256×192 原子批次（103.6s，0.25s 轮询）期间 `completed()` 全程为 0、`total()` 正确为 256，仅在计算结束瞬间跳到 256；ACE 计算 256×64 期间 `completed()` 与 `total()` 均恒为 0（内核甚至未 reset）。直接后果：宿主无法展示计算内进度，GUI 进度条在引擎阶段只能保持平直。

> **0.2.6 复核：已修复**（2026-08-29 实测：DPA4C 128×192 计算进行至 2.0s 时 `completed=4, total=128`，逐帧检查点推进；协作取消不受影响——中途 `control.cancel()` 后 0.00s 内抛 `CancelledError`）。Studio 侧已实现 §3 映射：`descriptor_service._report_engine_progress` 轮询计数器并映射到计算相位（占进度条 [_LOAD_BAR_SHARE, 1]）；0.2.5 引擎下计数器恒 0，进度条保持相位基线，行为仍诚实。

## 已验证无问题（曾怀疑，澄清）

- **ValleOganov 构建失败**：非 bug——其 `input.periodicity = ["fully_periodic"]`，用孤立结构测试被正确拒绝；传 periodic 数据集即正常。
- 27/28 描述符均可用自身 schema 的 required 参数成功构建（scan 全量通过）。
- `get_runtime_info` / schema 顶层 13 键 / `ComputeControl` 六方法与 §8 契约一致。
