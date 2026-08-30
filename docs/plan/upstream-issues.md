# mdescriptor 上游待修改清单（基于 0.2.7 复测）

> 日期：2026-08-30；环境：Windows x64，cp312 wheel **0.2.7**，项目 `.venv`（Python 3.12.9）+ numpy 2.5.2
> 来源：`engine-known-issues.md` 的历史复核与 0.2.7 `scripts/probe_engine.py` 探测；schema 事实基线见 `engine-api-report.md`/`.json`
> 范围：仅列 0.2.7 复测后仍需修改的项。0.2.7 已补齐 GUI baseline API、`StructureBatch.from_frames()`、详细描述符列表和 `UnsupportedPeriodicityError`；参数展示元数据也已完整提供。

| # | 问题 | 优先级 | 类型 |
|---|---|---|---|
| 1 | 无任何 GPU 执行路径（28/28 `devices: ["cpu"]`） | P1 | 能力 |

0.2.5 的原始条目证据保留在下文，作为变更历史；其中 #1/#3/#4/#5 已由 0.2.7 的公开 API 解决。

---

## 历史条目（0.2.5，已由 0.2.7 复核）

## 1. 权威契约文档 `gui-adaptation-baseline.md` 不随 wheel 发布

**问题**：`docs/gui-adaptation-baseline.md` 自述为 "the versioned contract for a GUI that discovers and runs MDescriptor descriptors"——JSON 字段、schema 版本、backend/execution_engine 语义、输入策略、结构化错误 payload、Windows 单线程启动指引的唯一权威来源。但该文件只存在于源码仓库，未进入发布物。

**证据**（0.2.5 实测）：

```text
python -c "import importlib.metadata as m; \
print([str(f) for f in m.distribution('MDescriptor').files if 'baseline' in str(f).lower()])"
# -> []   （wheel 内仅有 NOTICE/LICENSE 类文本）
```

**后果**：所有通过 PyPI 安装的 GUI/嵌入宿主拿不到契约；「schema 与契约是否一致」对公开用户无法自证，只能逐版本实测（Studio 侧就是这么做的）。

**建议修法**（任选其一或组合）：
- `package_data` / MANIFEST.in 将其收进 wheel（如 `mdescriptor/docs/gui-adaptation-baseline.md`），下游用 `importlib.resources` 读取；
- 或提供编程访问，如 `mdescriptor.gui_baseline()` 返回契约文本/字典，并在 `get_runtime_info()` 中附带 `baseline_version`；
- 至少在包级 `__doc__` 与 README 中给出指向仓库文件的链接。

**收益**：成本低；把「契约权威来源悬空」彻底关闭。

## 2. 无任何 GPU 执行路径

**问题**：0.2.3 与 0.2.5 的 28 个描述符全部 `execution.devices == ["cpu"]`。schema 的 devices 能力位设计（§8.3）是好的，但没有内容；wheel 未暴露 CUDA 路径。

**证据**：`scripts/probe_engine.py` 全量扫描，`devices` 无一含 `cuda`。

**后果**：对最重的模型描述符 DPA4/DPA4C 影响最大——0.2.5 虽已支持协作取消，长数据集仍只能纯 CPU 硬算；GUI 侧无法为 GPU 预留任何 UI。

**建议修法**：
- 若已有/计划 CUDA kernel：暴露 `execution.devices: ["cpu", "cuda"]` 能力位，发布对应 wheel，并说明设备选择入口（参数？环境变量？ExecutionOptions？）；
- 暂不实现也建议在 README / baseline 文档写明 roadmap 与判断方式，让下游不必猜测。

## 3. `StructureBatch` 缺 `from_frames()` 类构造器

**问题**：当前构造必须手拼 6 个数组（`numbers/positions/cells/pbc/offsets/ids`），其中 `offsets` 要求 **n+1 累积哨兵**——这是下游最容易写错的一处（长度写错、忘记 cumsum、int32 溢出都发生在这里）。

**证据**：Studio 侧不得不自行封装 `to_structure_batch()`（`mdescriptor_studio_backend/mdescriptor_adapter.py`）做完全相同的拼装，且构造器对错误 offsets 的报错时机偏晚、信息偏泛。

**建议修法**：

```python
@classmethod
def from_frames(cls, frames: Iterable[FrameLike]) -> "StructureBatch":
    """FrameLike: numbers, positions, cell(3x3), pbc(3), id"""
```

内部生成 offsets；顺带在现有构造器中前置校验「offsets 长度 == n+1 且单调递增」「ids 长度 == n」，给出明确错误信息。

**收益**：消除一类高频下游 bug；Studio 封装可退役，回归引擎官方路径。

## 4. `list_descriptors()` 返回裸名字 `tuple`

**问题**：仅返回名字元组，下游要逐个 `describe_descriptor(name)` 才能拿到 `category` / `descriptor_version` / `level` 做列表页分组、排序或版本比对——28 个描述符意味着 28 次调用。

**建议修法**：
- 最小改动：返回 `list` 而非 `tuple`（向后兼容风险极低）；
- 更优：`list_descriptors(detailed=True)` 返回记录数组（`name / descriptor_version / display_name / category / level`），一次调用可渲染描述符列表页。

## 5.（可选）`DescriptorInputError` 无独立子类

**问题**：0.2.5 新增的结构化 `code` / `path` / `details` 已解决机器可读分流（Studio 现按 `exc.code == "unsupported_periodicity"` 映射错误码），独立异常类不再是刚需，故列 P3、可不做。

**建议修法**（若做）：`UnsupportedPeriodicityError(DescriptorInputError)` 薄子类，保留 `code="unsupported_periodicity"` 与现有 payload，便于 `except` 分支写法与向后兼容；其余 `DescriptorInputError` 维持不变。

---

## 附：复核方法（issue 证据可复现）

- schema 全量探测：`scripts/probe_engine.py --out report.json`（只读，不实例化、不加载模型）
- 逐条功能复测：`scripts/verify_known_issues.py`（含死锁子进程复现、混合周期批次实测、DPA4 中途取消实测、wheel 文件清单检查、结构化错误 payload 检查）
- 基线对照：`docs/plan/engine-api-report.json`（0.2.3 → 0.2.5 diff）
