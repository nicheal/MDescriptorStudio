# mdescriptor 已知问题清单（基于 0.2.3 实测）

> 日期：2026-08-29；环境：Windows x64，cp312 wheel，项目 `.venv`
> 性质：MDescriptor Studio 开发过程中的实测发现，可直接作为上游 issue 素材（github.com/nicheal/MDescriptor）
> 注：PyPI 已出现 0.2.4，以下问题是否仍存在需在升级后按本清单逐条复核

## 高优先级

### 1. 懒加载 import 死锁（严重）
`create_descriptor` 内部懒加载原生扩展模块；当进程内存在**阻塞读取 stdin 的线程**时，构建永久挂起，直到 stdin EOF 才放行。最小复现：工作线程执行 `create_descriptor("ACE", …)` + 另一线程 `for line in sys.stdin`（管道不关闭）→ 构建阻塞 15s+（= EOF 时刻）。直接后果：任何嵌入宿主进程的用法（GUI sidecar、notebook 管道）都必须在单线程阶段预热全部描述符。Studio 侧已用启动预热规避（05 文档 §6）。建议：引擎导入期预解析全部原生模块，或定位 import 链上的阻塞点。

### 2. `backend` 标注与实际执行路径不一致
`describe_descriptor("DPA4"/"DPA4C")["backend"] == "numpy"`，但内核自述 "native C++ core and NumPy fallback"，且实测 bundled 检查点实例化后持有 `_native.Dpa4Calculator` / `Dpa4cCalculator` —— **实际计算走 C++**。`backend` 字段当前语义是"适配器实现族"而非执行路径，会误导消费者（GUI 按 Rule 5 如实显示 numpy）。建议：区分 `implementation`（适配器）与 `execution_engine`（实际内核）两个字段，或 backend 取值反映真实路径。

### 3. DPA4/DPA4C 不支持协作取消
`execution.cooperative_cancel == false`（对比 NEP 为 true）。两者恰是最重的模型描述符，整数据集计算可能数十分钟且**无法中断**，只能等其自然结束。建议实现 ComputeControl 检查点（模型按帧循环处天然可插）。

## 中优先级

### 4. 无 per-descriptor 版本号
`list_descriptors()` / `describe_descriptor()` 均不含版本信息；`get_runtime_info` 只有引擎整体版本。下游无法做 per-descriptor 的结果溯源/缓存失效区分（Studio 的 `descriptor_runs.descriptor_version` 只能记引擎版本）。

### 5. 周期性校验的错误信息不统一且含糊
- 5 个仅周期描述符（EwaldSumMatrix、LMBTR、MBTR、SineMatrix、ValleOganov）在 isolated 输入下报 *"X does not support input field 'periodicity'"*——措辞像调用方传错参数名，实际含义是"仅支持 fully_periodic"；
- **LodeSphericalExpansion** 同场景报 *"cell matrix is singular"*——输入能力校验发生在晶胞检查之后，错误归因误导。
建议：统一为 `UNSUPPORTED_PERIODICITY` 语义（"requires fully_periodic"），且 capability 校验先于数值检查。

### 6. 混合周期性（mixed periodicity）被整体拒绝
`StructureBatch` 文档明言 "Mixed periodicity is intentionally rejected"——同一批次内部分周期结构（表面+分子、slab+vacuum）直接报错，无逐结构路由。对实际训练集是硬限制；GUI 已用预检规避（禁用不兼容描述符）。建议：至少提供 per-structure 分批计算的上层封装。

### 7. 正式基线文档缺失
设计文档头部引用的 `gui-adaptation-baseline.md`（"基线定义唯一的 JSON 字段、版本、模型资源和执行契约"）既不在 PyPI 发布物也不在仓库 docs 中。schema 受控字段清单（§8.2）与实际存在出入（见 #8），契约的唯一权威来源悬空。

## 低优先级

### 8. schema 契约与实际的小偏差
- §8.2 受控类型清单含 `string`，0.2.3 实测 0 处使用（8 种类型：species/integer/number/boolean/enum/array/object/model）；
- `object` 参数（21 处，如 ACE 的 `trans`/`D`）与嵌套 `properties` 未在 §8.1 示例/§8.2 清单中定义——但这是真实且有用的能力，建议补进契约而非删能力。

### 9. 0.2.3 全部 `devices: ["cpu"]`
无任何 GPU 条目。§8.3 的 devices 能力位设计正确，但当前 wheel 未暴露 CUDA 路径（对大模型描述符 DPA4 影响最大）。

### 10. API 人体工学
- `StructureBatch.offsets` 要求 n+1 哨兵（累积式），无 `from_frames()` 类辅助构造器，易错（Studio 已封装）；
- `list_descriptors()` 返回 `tuple`（而非常见 list/record 数组）；
- `DescriptorInputError` 同时承担"周期性不兼容"与"数据集无效"两类语义，细分有利上游映射错误码。

## 已验证无问题（曾怀疑，澄清）

- **ValleOganov 构建失败**：非 bug——其 `input.periodicity = ["fully_periodic"]`，用孤立结构测试被正确拒绝；传 periodic 数据集即正常。
- 27/28 描述符均可用自身 schema 的 required 参数成功构建（scan 全量通过）。
- `get_runtime_info` / schema 顶层 13 键 / `ComputeControl` 六方法与 §8 契约一致。
