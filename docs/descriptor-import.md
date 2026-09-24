# 自定义描述符导入与导出

先注册计算描述符所用的结构数据集，再在「结果」页选择「导入描述符」。导入后可选中结果并打开「分析」。无须安装描述符插件。

交换格式为 NumPy NPZ，不允许 pickle/object 数组：

- `values`：二维有限实数矩阵 `[样本数, 特征数]`，不能包含 NaN 或 Inf。
- `metadata`：JSON 字符串标量，包含 `descriptor`（名称）、`row_semantics`（`structure` 或 `atom`）；建议填写 `descriptor_version` 和 `configuration`，区分不同特征定义和参数。
- `row_offsets`：仅原子级使用，整数数组，长度为帧数加一。从 0 开始，累积每帧原子数，最后一个值等于矩阵行数。

结构级每帧一行，按当前数据集的帧顺序排列。原子级先按帧排列，再按该帧的原子顺序排列。程序校验数量，无法证明用户是否采用了正确的物理顺序；请由描述符作者保证列定义和行顺序一致。

```python
import json
import numpy as np

# X.shape == (数据集帧数, 特征数)
np.savez_compressed(
    "custom.npz", values=X,
    metadata=json.dumps({
        "descriptor": "MyDescriptor",
        "descriptor_version": "1.0",
        "row_semantics": "structure",
        "configuration": {"cutoff": 5.0},
    }),
)

# 原子级：X 按帧与原子顺序排列；atom_counts 为每帧原子数
np.savez_compressed(
    "custom_atom.npz", values=X_atom,
    row_offsets=np.concatenate(([0], np.cumsum(atom_counts))),
    metadata=json.dumps({
        "descriptor": "MyAtomDescriptor",
        "descriptor_version": "1.0",
        "row_semantics": "atom",
        "configuration": {},
    }),
)
```

默认覆盖整个数据集。单帧文件可在 metadata 中设置 `scope: "frame"` 与从 0 起算的 `frame_index`。不支持任意重排或缺帧；应先准备对应数据集。

每行操作栏的导出图标可导出该行的已完成描述符结果，无须先选中，保留矩阵精度、元数据及原子行偏移，不包含结构文件或分析历史。导入结果的设备列显示「导入」。导出文件携带数据集指纹，再导入时必须匹配；自建文件可省略指纹，由作者保证结构对应。导入和导出均在本机完成。
