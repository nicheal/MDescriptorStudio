# MDescriptor Studio UI 与描述符分析开发文档

> 目标：在保持 **MDescriptor = 描述符计算核心** 的前提下，规划 MDescriptor Studio 的 UI 信息架构、交互逻辑和描述符空间分析功能。当前阶段明确不扩展到能量/力推理与训练，重点是 **descriptor-space analysis**。

## 1. 项目定位与边界

MDescriptor Studio 定位为：

> **面向原子结构数据集的描述符计算与描述符空间分析工作台（Descriptor-space Analysis Workbench）**

核心数据流：

```text
Dataset
   ↓
Explore
   ↓
Descriptor Run
   ↓
Analysis
   ↓
发现特殊结构 / 局域环境 / 数据覆盖问题
   ↓
Explore
```

职责边界固定为：

```text
MDescriptor
= Describe
= 描述符计算引擎

MDescriptor Studio Backend
= Analyze
= 数据集与描述符空间分析

React UI
= Visualize & Interact
= 可视化、筛选、联动与交互
```

当前明确不做：

- Energy / Force / Virial prediction
- Potential / Calculator API
- 势函数训练
- 优化器、Loss、Checkpoint
- 分布式训练
- 模型推理基准平台

如果未来确有需要，再设计与 `Descriptor` 平行的 `Potential` API，但不进入当前路线。

---

## 2. 总体软件架构

```text
┌─────────────────────────────────────────────┐
│ React + TypeScript                          │
│ Dataset / Explore / Descriptor / Analysis   │
│ ECharts / 3D Structure Viewer               │
└────────────────────┬────────────────────────┘
                     │ Tauri IPC
┌────────────────────▼────────────────────────┐
│ MDescriptor Studio Python Backend           │
│ Dataset Layer                               │
│ Analysis Layer                              │
│ Job Manager                                 │
│ Result / Cache / SQLite                     │
└────────────────────┬────────────────────────┘
                     │ Stable Python API
┌────────────────────▼────────────────────────┐
│ MDescriptor                                 │
│ StructureBatch                              │
│ Descriptor Registry                         │
│ Descriptor.compute                          │
│ DescriptorResult                            │
│ ComputeControl                              │
│ Python → pybind11 → C++17                   │
└─────────────────────────────────────────────┘
```

核心原则：

1. React 不执行 PCA、UMAP、FPS 等算法。
2. React 不直接调用 C++。
3. GUI 不复制 MDescriptor 描述符实现。
4. MDescriptor 不承载 Dataset/Analysis 逻辑。
5. 所有大规模数值分析在 Python Backend 完成。
6. 大数组不经 JSON IPC 全量传输。
7. 分析结果必须可缓存、可复现、可追溯。

---

## 3. UI 总体信息架构

一级导航长期固定为：

```text
Datasets
Explore
Descriptors
Analysis
```

不要随着分析功能扩展继续增加一级菜单。

建议主窗口结构：

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ MDescriptor Studio                                      Jobs  ⚙   ─ □ ×     │
├──────────────────┬───────────────────────────────────────────────────────────┤
│                  │  Current Dataset                                         │
│ DATASETS         │  DeepMD · 12,480 structures · Ga As · Energy · Force    │
│                  ├───────────────────────────────────────────────────────────┤
│ ▌ GaAs Training │                                                           │
│   GaAs Test      │  Datasets   Explore   Descriptors   Analysis             │
│   Si AIMD        │                                     ────────              │
│                  │                                                           │
│ + Add Dataset    │                    WORKSPACE                              │
│                  │                                                           │
├──────────────────┴───────────────────────────────────────────────────────────┤
│ Ready · MDescriptor x.y.z · CPU 16T · Memory 2.1 GB                        │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 3.1 全局视觉定位

推荐视觉语言：

> **Fluent Scientific Workbench**

关键词：

- Clean
- Dense
- Technical
- Calm
- Precise

避免：

- 过度卡片化
- 大面积渐变
- 玻璃拟态
- 巨大标题
- 过多圆角
- 典型后台管理 Dashboard 风格
- 视觉效果压过科学数据本身

### 3.2 全局布局

推荐：

- Dataset Sidebar：220–240 px
- Current Context Bar：64–72 px
- 主工作区：自适应
- Analysis Main Canvas：70–75%
- Analysis Inspector：25–30%
- Bottom Status Bar：薄状态行
- Jobs：右侧 Drawer

### 3.3 全局 Workspace State

```ts
interface WorkspaceState {
  activeDatasetId: string | null
  activeFrameIndex: number
  activeDescriptorRunId: string | null
  activeAnalysisId: string | null
}
```

Dataset 是全局 Context，不在每个页面重复选择 Dataset。

---

## 4. Dataset Sidebar

建议：

```text
DATASETS                             ＋

Search datasets...

▌ GaAs Training
  DeepMD · 12.5k

  GaAs Test
  extxyz · 2.1k

  Si AIMD
  extxyz · 20k

+ Add Dataset
```

当前 Dataset 使用浅色选中背景和左侧 2–3 px Accent Bar，不采用整块高饱和主色。

切换 Dataset 后：

```text
activeDatasetId
    ↓
Datasets
Explore
Descriptors
Analysis
```

统一刷新。

---

## 5. Current Dataset Context Bar

所有页面顶部保持当前 Dataset 信息：

```text
GaAs Training Set

DeepMD · 12,480 structures · 798,720 atoms · Ga As

Energy ✓   Force ✓   Virial ✓   PBC XYZ
```

右侧可显示：

```text
D:\datasets\GaAs\
```

作用：

- 防止用户失去上下文
- 所有分析结果始终知道属于哪个 Dataset
- 为多 Dataset 比较提供明确参考

---

## 6. Datasets 页面

### 6.1 页面目标

回答：

> “这个数据集是什么？”

而不是：

> “这个数据集在描述符空间是什么形状？”

### 6.2 页面结构

```text
Dataset Overview
─────────────────────────────────────────────────────

Structures        12,480
Atoms             798,720
Elements          Ga · As
Format            DeepMD
Properties        Energy · Force · Virial
PBC               XYZ

Distributions
─────────────────────────────────────────────────────

┌─────────────────────────┐ ┌─────────────────────────┐
│ Energy / atom           │ │ Force magnitude         │
│ histogram               │ │ histogram               │
└─────────────────────────┘ └─────────────────────────┘

┌─────────────────────────┐ ┌─────────────────────────┐
│ Atoms / structure       │ │ Cell volume             │
│ histogram               │ │ histogram               │
└─────────────────────────┘ └─────────────────────────┘
```

### 6.3 MVP 统计

- Number of structures
- Elements
- Atoms / structure
- Energy / atom
- Force magnitude
- Max force / structure
- Cell volume
- PBC
- Dataset fingerprint

后续质量检查：

- NaN / Inf
- invalid cell
- extremely short distances
- extreme force
- duplicate / near-duplicate structures

---

## 7. Explore 页面

Explore 是整个应用的“结构检查终点”。

任何分析结果，例如 PCA point、UMAP point、Outlier、Nearest Neighbor、Cluster member、FPS selected structure、Coverage failure，都应该能回到 Explore。

### 7.1 主布局

```text
┌───────────────────────────────────────────────────────────────────────┐
│ Frame 328 / 12480       ‹   ›   Random        Fit   Supercell 1×1×1 │
├──────────────────────────────────────────────┬────────────────────────┤
│                                              │ STRUCTURE              │
│                                              │                        │
│                                              │ Formula     Ga32As32   │
│                3D VIEWER                     │ Atoms       64         │
│                                              │ E / atom    -4.281 eV  │
│                                              │ Max |F|      0.32      │
│                                              │ Volume      728 Å³     │
│                                              │                        │
│                                              │ CELL                   │
│                                              │ a ...                  │
│                                              │ b ...                  │
│                                              │ c ...                  │
├──────────────────────────────────────────────┴────────────────────────┤
│ Atoms                                                                 │
│ #    Element      x        y        z        Fx       Fy       Fz     │
└───────────────────────────────────────────────────────────────────────┘
```

### 7.2 统一联动

所有分析页面提供：

```text
[Open in Explore]
```

内部行为：

```text
activeFrameIndex = selectedSample.structure
→ Navigate Explore
→ Viewer refresh
→ 若为 atom-level，继续高亮 local_atom
```

Atom-level 样本：

```text
sample = [structure, local_atom]
```

应进一步支持：

- 自动打开对应 Frame
- 高亮对应 Atom
- 显示 cutoff sphere
- 显示 neighbor environment

---

## 8. Descriptors 页面

### 8.1 目标

只负责：

> 配置、执行、保存 Descriptor Run

不在该页面做 PCA / UMAP / Outlier 等分析。

### 8.2 页面布局

建议左 60% 为 Configuration，右 40% 为 Descriptor Information：

```text
ACE
Atomic Cluster Expansion
───────────────────────────────────────────────────────────────

Configuration                              Descriptor Information
                                           ──────────────────────
Species                                    Level       Atom
[ Ga ] [ As ]                              Backend     C++
                                           Features    512
Correlation order                          Sparse      Yes
[ 3                ]                       Cancel      Yes

Maximum degree                             Current Dataset
[ 8                ]                       GaAs Training

Cutoff
[ 5.000            ] Å

───────────────────────────────────────────────────────────────

Execution

Device
CPU

Threads
[ 16 ]

Scope
○ Current frame
● Entire dataset

Output
● float64
○ float32

                                        [ Calculate ]
```

完成后：

```text
ACE run #17 completed

12,480 structures
798,720 atoms
512 features
02:31

[ Open in Analysis ]
```

### 8.3 Descriptor UI 规则

Descriptor 页面不得：

- hard-code descriptor list
- hard-code descriptor parameter names
- 根据 `inspect.signature()` 猜参数

必须来自 MDescriptor 的机器可读 API：

```python
list_descriptors()
describe_descriptor(name)
```

---

## 9. Analysis 页面总体架构

Analysis 是软件未来功能密度最高的区域。

顶部选择 Descriptor Run：

```text
Descriptor Run
[ ACE · Run 017 · rcut 5.0 · 512 features ▼ ]
```

第二层导航固定为：

```text
Overview
Projection
Similarity
Clusters
Outliers
Sampling
Coverage
Compare
```

主结构：

```text
┌─────────────────────────────────────────────────────────────────────────┐
│ Analysis                                                                │
│ Descriptor Run [ ACE · Run 017 ▼ ]                                     │
├─────────────────────────────────────────────────────────────────────────┤
│ Overview | Projection | Similarity | Clusters | Outliers | Sampling    │
│          | Coverage | Compare                                           │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│                           Analysis Workspace                            │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

不要为 PCA / UMAP / KMeans / FPS 分别创建一级页面。

---

## 10. Analysis Overview

用户打开 Descriptor Run 后先进入摘要，而不是直接进入 PCA。

```text
ACE · Run 017
────────────────────────────────

Level              Atom
Features           512
Rows               798,720
Structures         12,480
dtype              float64
Cutoff             5.0 Å
Correlation order  3
Engine             MDescriptor x.y.z
```

分析摘要：

```text
Projection
PC1 + PC2                  68.4 %

Feature Analysis
Near-zero variance         21

Outlier
Detected                   83

Sampling
Not calculated

Coverage
Not calculated
```

Overview 中的 section 可跳转至对应模块。

---

## 11. Projection

Projection 统一承载：

- PCA
- UMAP
- t-SNE（后续，可选）

不要拆成多个页面。

### 11.1 页面布局

```text
Projection

Method
[ PCA ▼ ]

X Axis
[ PC1 ▼ ]

Y Axis
[ PC2 ▼ ]

Color by
[ Energy / atom ▼ ]

Explained variance: 81.4%

┌──────────────────────────────────────────────┬─────────────────────┐
│                                              │ SELECTED SAMPLE     │
│                                              │                     │
│                PCA / UMAP                    │ Frame       1837    │
│                                              │ Formula     Ga32As32│
│                                              │ E/atom      -4.18   │
│                                              │ Max |F|      0.42   │
│                                              │ Cluster      3      │
│                                              │                     │
│                                              │ [Open in Explore]   │
└──────────────────────────────────────────────┴─────────────────────┘
```

### 11.2 Color By

结构级：

- Frame
- Energy / atom
- Max force
- Volume
- Cluster
- Outlier score
- Nearest-reference distance
- Dataset

原子级：

- Element
- Structure
- Atom index
- Cluster
- Outlier score
- Local environment label

### 11.3 PCA 附加分析

- Explained variance ratio
- Cumulative explained variance
- Effective dimension
- 2D / 3D projection
- Projection result caching

---

## 12. Similarity

### 12.1 目标

回答：

> 当前结构/原子环境与哪些样本最相似？

以及：

> 整个数据集的描述符距离关系是什么？

### 12.2 支持方法

首期：

- Euclidean distance
- Cosine similarity

后续：

- Standardized Euclidean
- 归一化自定义距离

### 12.3 页面布局

```text
Similarity

Metric
[ Euclidean ▼ ]

Reference
● Selected structure
○ Pairwise dataset

Frame
[ 1837 ]

┌────────────────────────────────────────────┬────────────────────────┐
│                                            │ NEAREST NEIGHBORS      │
│         Distance / Similarity Plot         │                        │
│                                            │ #   Frame   Distance   │
│                                            │ 1   2918    0.012      │
│                                            │ 2   772     0.019      │
│                                            │ 3   9201    0.024      │
│                                            │                        │
│                                            │ [Open in Explore]      │
└────────────────────────────────────────────┴────────────────────────┘
```

### 12.4 大数据约束

- 不默认生成完整 `N × N` distance matrix
- 优先采用 kNN
- 小数据集才允许完整 heatmap
- 后续可使用 approximate nearest neighbor

---

## 13. Clusters

首期算法：

- K-Means
- DBSCAN

后续：

- HDBSCAN
- Agglomerative clustering

页面：

```text
Clusters

Method
[ K-Means ▼ ]

Clusters
[ 6 ]

[ Run ]

┌──────────────────────────────────────────────┬───────────────────────┐
│                                              │ CLUSTERS              │
│             PCA / UMAP                       │                       │
│             color by cluster                 │ Cluster 0     4,210   │
│                                              │ Cluster 1     2,817   │
│                                              │ Cluster 2     1,421   │
│                                              │ ...                   │
└──────────────────────────────────────────────┴───────────────────────┘
```

注意：

> Cluster 是 descriptor-space cluster，不直接等同于物理相。

---

## 14. Outlier Detection

### 14.1 支持方法

首期：

- kNN distance

后续：

- Local Outlier Factor
- Isolation Forest
- Mahalanobis distance

### 14.2 页面布局

```text
Outlier Detection

Method
[ kNN Distance ▼ ]

Neighbors
[ 10 ]

[ Analyze ]

───────────────────────────────────────────────────────────────

Outlier Score Distribution
[ histogram ]

───────────────────────────────────────────────────────────────

Top Outliers

Rank   Frame   Score   E/atom   Max Force
1      1837    0.982   -3.18    5.82
2      2911    0.951   -3.42    4.91
...
```

点击任意行：

```text
→ Explore
```

后续可组合：

```text
Descriptor outlier
+
Energy outlier
+
Force outlier
```

这里的 energy/force 是 Dataset reference properties，不是模型预测值。

---

## 15. Sampling

首要算法：

> Farthest Point Sampling（FPS）

目标：从大数据集中选择描述符空间中具有代表性的结构。

页面：

```text
Representative Sampling

Method
[ Farthest Point Sampling ]

Input
12,480 structures

Target structures
[ 1000 ]

Initial seed
[ Automatic ▼ ]

[ Run Sampling ]
```

完成后：

```text
Selected
1000 / 12480

Coverage
97.2%

┌──────────────────────────────────────────────────────────────┐
│ PCA / UMAP                                                   │
│ Gray       original                                          │
│ Highlight  selected                                          │
└──────────────────────────────────────────────────────────────┘

[ Save Selection ]
[ Export Frames ]
```

后续扩展：

- random sampling
- stratified sampling
- cluster representative sampling
- per-element local-environment sampling

---

## 16. Coverage

### 16.1 目标

衡量 Query Dataset 是否被 Reference Dataset 的描述符空间充分覆盖。

典型 nearest-reference distance：

\[
d_i = \min_{j \in A} \|\mathbf d_i - \mathbf d_j\|
\]

其中：

- A = Reference Dataset
- i = Query Dataset 中的样本

### 16.2 页面布局

```text
Coverage

Query Dataset
GaAs Test
2,100 structures

Reference Dataset
[ GaAs Training ▼ ]
12,480 structures

Descriptor
[ ACE compatible run ▼ ]

Distance Metric
[ Euclidean ▼ ]
```

结果：

```text
Coverage Summary
────────────────────────────

Covered            91.3%
Marginal            6.1%
Out of coverage     2.6%
```

可视化：

```text
PCA / UMAP overlay

Reference    gray
Query        colored by nearest distance
```

### 16.3 Train/Test overlap

Coverage 内同时支持：

- Train → Test coverage
- Train/Test nearest-neighbor overlap
- Near-duplicate detection
- Potential leakage inspection

对于连续 AIMD 轨迹随机拆分尤其重要。

---

## 17. Compare

Compare 用于不同 Descriptor Run 之间的比较。

例如：

```text
Descriptor A
[ ACE · Run 17 ▼ ]

Descriptor B
[ SOAP · Run 21 ▼ ]
```

首期指标：

- Distance correlation
- Spearman rank correlation
- Neighbor consistency
- Effective dimension
- Feature count
- Runtime
- Memory（若可可靠获取）

摘要：

```text
Distance correlation       0.91
Neighbor consistency       87.4%

Effective dimension
ACE                         27
SOAP                        34
```

双投影：

```text
┌───────────────────────────┬───────────────────────────────┐
│ ACE                       │ SOAP                          │
│                           │                               │
│ PCA / UMAP                │ PCA / UMAP                    │
│                           │                               │
└───────────────────────────┴───────────────────────────────┘
```

---

## 18. Feature Analysis

Feature Analysis 可以先作为 `Compare` 的内部子模块，未来视功能密度决定是否独立为二级页。

### 18.1 Feature Variance

计算：

\[
\mathrm{Var}(d_k)
\]

输出：

- feature variance spectrum
- near-zero variance features
- effective non-zero dimensions

### 18.2 Feature Correlation / Redundancy

计算：

\[
C_{ij} = \mathrm{corr}(d_i, d_j)
\]

输出：

- feature correlation heatmap
- highly correlated feature count
- redundancy ratio

### 18.3 Effective Dimensionality

基于 PCA：

- PC1–PC2 explained variance
- PC1–PC10
- 95% variance dimension
- 99% variance dimension

---

## 19. Atom-level Local Environment Analysis

对于 `DescriptorResult.level == "atom"`：

```text
one point = one atom / local environment
```

推荐支持：

- atom-level PCA
- atom-level UMAP
- local environment clustering
- atom-level outlier
- nearest local environment
- per-element filtering

例如：

```text
Color by
[ Element ▼ ]

Ga environments
As environments
```

点击某原子点：

```text
sample = [structure, local_atom]
    ↓
Explore
    ↓
Frame = structure
SelectedAtom = local_atom
```

后续可显示：

- cutoff sphere
- neighbor list
- coordination
- local environment comparison

---

## 20. Trajectory Analysis

对于有明确时间顺序的 AIMD / MD Dataset：

### 20.1 Descriptor Distance vs Time

\[
D(t) = \|\mathbf d(t) - \mathbf d(0)\|
\]

可用于观察：

- phase transition
- relaxation
- structural drift
- defect migration

### 20.2 PCA / UMAP Trajectory Path

```text
Frame 0 → 1 → 2 → 3 → ...
```

在 projection 上连接连续帧。

后续可支持：

- descriptor velocity
- abrupt structural event detection
- trajectory segmentation

---

## 21. Dataset Drift

比较两个 Dataset / Dataset version：

```text
Dataset A
vs
Dataset B
```

回答：

- 新数据是否只是旧数据重复？
- 是否扩展 descriptor space？
- 新增空间主要在哪里？
- 哪些区域是 novel region？

输出示意：

```text
Existing space        78 %
Expanded boundary     17 %
Novel region           5 %
```

---

## 22. Descriptor Parameter Sensitivity

用于比较同一种 Descriptor 的不同参数。

例如：

```text
ACE rcut = 4
ACE rcut = 5
ACE rcut = 6
```

比较：

- feature count
- effective dimension
- pairwise distance correlation
- neighbor ranking stability
- PCA topology
- clustering stability
- runtime

MDescriptor 只负责分别计算 Descriptor Run，Studio Analysis Layer 负责比较这些 Run。

---

## 23. Analysis Layer 目录与 API

建议目录：

```text
backend/
└── mdescriptor_studio_backend/
    ├── analysis/
    │   ├── reduction.py
    │   ├── similarity.py
    │   ├── neighbors.py
    │   ├── clustering.py
    │   ├── outlier.py
    │   ├── sampling.py
    │   ├── coverage.py
    │   ├── comparison.py
    │   ├── features.py
    │   └── trajectory.py
```

推荐 API：

```python
run_pca(result, ...)
run_umap(result, ...)

nearest_neighbors(result, ...)
pairwise_similarity(result, ...)

run_kmeans(result, ...)
run_dbscan(result, ...)

detect_outliers(result, ...)

farthest_point_sampling(result, ...)

compute_coverage(reference, query, ...)

compare_descriptors(result_a, result_b, ...)

feature_variance(result, ...)
feature_correlation(result, ...)
effective_dimension(result, ...)

analyze_trajectory(result, ...)
```

---

## 24. AnalysisResult 统一结果模型

不要让每个分析算法自行发明存储格式。

建议：

```python
@dataclass
class AnalysisResult:
    id: str
    analysis_type: str
    input_run_ids: tuple[str, ...]
    parameters: dict
    data_path: str
    metadata: dict
    created_at: str
```

不同分析的数据放入文件存储：

```text
analysis/
└── analysis_<uuid>/
    ├── metadata.json
    ├── coordinates.npy
    ├── labels.npy
    ├── scores.npy
    ├── indices.npy
    └── ...
```

大数组不得直接存入 SQLite。

---

## 25. DescriptorRun

建议保持：

```text
id
dataset_id
descriptor_name
descriptor_version
engine_version
parameters_json
scope
status
created_at
started_at
finished_at
result_path
error_message
```

AnalysisResult 通过 `input_run_ids` 关联 DescriptorRun。

---

## 26. JobManager

所有耗时分析统一走 JobManager：

```text
QUEUED
RUNNING
COMPLETED
FAILED
CANCELLED
```

任务包括：

- Descriptor calculation
- Dataset statistics
- PCA
- UMAP
- Similarity
- Clustering
- Outlier
- FPS
- Coverage
- Descriptor comparison
- Trajectory analysis

Job：

```text
job_id
job_type
dataset_id
descriptor_run_id
analysis_id
status
progress
message
started_at
finished_at
```

UI 通过右侧 Jobs Drawer 统一查看。

---

## 27. IPC 建议

分析相关 IPC：

```text
analysis.list
analysis.get
analysis.delete

analysis.pca
analysis.umap

analysis.neighbors
analysis.similarity

analysis.cluster
analysis.outlier

analysis.fps
analysis.coverage

analysis.compare
analysis.feature_variance
analysis.feature_correlation
analysis.effective_dimension

analysis.trajectory
```

所有长任务返回：

```text
job_id
```

前端订阅：

```text
job.progress
job.completed
job.failed
```

---

## 28. 大数据量处理规则

必须坚持：

> 不通过 JSON IPC 传输完整 descriptor matrix。

例如：

```text
798,720 atoms × 512 features
```

不能直接送给 React。

处理流程：

```text
DescriptorResult.values
    ↓
Python Backend
    ↓
PCA / UMAP / kNN / FPS / clustering
    ↓
仅输出必要 coordinates / labels / indices / small subsets
    ↓
React
```

大型 scatter：

- 分层抽样
- downsampling
- selected subset
- progressive loading
- 后续可加入 WebGL scatter

---

## 29. 缓存策略

Analysis Cache Key：

```text
SHA256(
    input_descriptor_run_id
  + analysis_type
  + canonical_parameters
  + analysis_backend_version
)
```

Coverage / Compare 等多输入分析：

```text
SHA256(
    sorted_input_run_ids
  + analysis_type
  + canonical_parameters
  + analysis_backend_version
)
```

命中时：

```text
Use Existing
Recalculate
```

---

## 30. 页面联动规则

### 30.1 Projection → Explore

```text
Click PCA / UMAP point
→ sample identity
→ activeFrameIndex
→ Explore
```

### 30.2 Similarity → Explore

```text
Click nearest neighbor
→ selected frame
→ Explore
```

### 30.3 Outlier → Explore

```text
Click outlier row
→ selected frame / atom
→ Explore
```

### 30.4 Cluster → Projection / Explore

```text
Select cluster
→ highlight cluster
→ filter sample list
→ open representative structure
```

### 30.5 Sampling → Dataset

```text
FPS result
→ selected frame IDs
→ save selection
→ export subset
```

### 30.6 Coverage → Explore

```text
Click uncovered structure
→ Explore
```

---

## 31. Analysis 右侧 Inspector 统一规则

所有 Analysis 页面统一采用：

```text
Main Canvas    70–75%
Inspector      25–30%
```

Inspector 根据模块变化：

```text
Projection
→ Selected Sample

Similarity
→ Selected Neighbor

Clusters
→ Selected Cluster

Outliers
→ Selected Outlier

Sampling
→ Selected Structure

Coverage
→ Selected Uncovered Structure

Compare
→ Selected Descriptor / Difference
```

这样可以显著降低用户学习成本。

---

## 32. 推荐开发优先级

### Phase A：Analysis 基础设施

先完成：

- AnalysisResult
- AnalysisService
- Analysis cache
- Job integration
- analysis IPC
- Analysis page shell
- DescriptorRun selector

### Phase B：第一批核心分析

按以下顺序：

```text
PCA
↓
Nearest Neighbors
↓
Similarity
↓
Outlier Detection
↓
Clustering
↓
FPS
↓
Coverage
↓
Train/Test Overlap
↓
Descriptor Comparison
```

### Phase C：描述符质量分析

加入：

- Feature variance
- Feature correlation
- Redundancy
- Effective dimension
- Descriptor distance correlation
- Neighbor ranking consistency
- Parameter sensitivity

### Phase D：高级局域与动态分析

加入：

- Atom-level PCA / UMAP
- Local environment clustering
- Atom outlier
- Local nearest neighbors
- Trajectory analysis
- Dataset drift
- Incremental coverage

---

## 33. Analysis MVP 建议

第一版 Analysis MVP：

```text
Overview
Projection
  └─ PCA

Similarity
  └─ Nearest Neighbors

Outliers
  └─ kNN Distance

Sampling
  └─ FPS

Coverage
  └─ Train/Test nearest-distance coverage

Compare
  └─ Distance correlation
```

这已经能形成完整闭环：

```text
Dataset
→ Descriptor
→ PCA
→ Find Outlier / Neighbor
→ FPS / Coverage
→ Explore
```

UMAP、HDBSCAN、LOF 等可以后续加入，不阻塞第一版。

---

## 34. 当前项目边界再次确认

当前开发主线：

```text
MDescriptor
──────────────────────────────
Descriptor Engine

MDescriptor Studio
──────────────────────────────
Dataset Management
Structure Explore
Descriptor Calculation
Descriptor-space Analysis
```

当前不进入：

```text
Potential inference
Energy prediction
Force prediction
Model training
```

保持该边界有利于：

- 控制项目复杂度
- 保持 MDescriptor API 稳定
- 避免依赖膨胀
- 突出描述符分析特色
- 更快完成可用 GUI
- 后续更容易独立抽取 Analysis package

---

## 35. 最终产品逻辑

```text
LOAD
Dataset

↓

EXPLORE
Structure

↓

DESCRIBE
Descriptor calculation

↓

ANALYZE
Projection
Similarity
Outlier
Cluster
Sampling
Coverage
Compare

↓

DISCOVER
Representative structures
Novel environments
Dataset gaps
Redundant samples
Descriptor differences

↓

EXPLORE
Back to physical structure
```

整个 UI 和 Analysis 开发都应围绕：

> **“描述符空间中的发现能够快速回到真实原子结构”**

这一主交互闭环设计。


