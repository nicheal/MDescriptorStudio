# MDescriptorStudio 描述符引导的数据集扩展：分阶段详细执行计划

> 文档类型：Implementation Plan  
> 项目：MDescriptorStudio  
> 功能名称：Descriptor-Guided Dataset Expansion（描述符引导的数据集扩展）  
> 日期：2026-09-24  
> 目标版本：Experimental → Stable  
> 建议实施顺序：G0 → G1 → G2 → G3 → G4 → G5

---

## 1. 项目目标

在 MDescriptorStudio 中新增一个独立的 **Generation / Dataset Expansion** 科学工作流，使软件不仅能够从已有数据集中“选择代表性样本”，还能够在结构空间中主动生成新候选结构，并利用 MDescriptor 描述符评价这些候选是否扩展了原有数据集的描述符空间与局域原子环境。

核心科学目标定义为：

\[
\boxed{\text{在物理/几何合理约束下，以有限描述符计算预算最大化数据集的结构与局域环境多样性}}
\]

该功能不以最低能量结构搜索为主要目标，而以以下指标为核心：

1. Structure novelty：新结构相对于已有数据的描述符新颖度。
2. Local-environment novelty：候选结构包含的新局域原子环境程度。
3. Coverage improvement：加入新结构后参考描述符域的覆盖改善。
4. Novel environment count：超过给定新颖度阈值的局域环境数量。
5. Search efficiency：单位 descriptor evaluation 获得多少有效新环境。

最终形成闭环：

```text
Dataset
  ↓
Descriptor
  ↓
Analysis / sparse-region detection
  ↓
Descriptor-Guided Generation
  ↓
Candidate evaluation
  ↓
Accepted structures
  ↓
Expanded Dataset
  ↓
Descriptor / Analysis
```

---

## 2. 与现有 Sampling 功能的边界

现有 `Dataset Sampling / Representative Sampling` 保留，不直接替换。

### 2.1 现有 Sampling

输入：已有候选结构集合。  
输出：已有结构的子集。

```text
Existing structures
       ↓
FPS / Random / Stratified / Cluster representative
       ↓
Selected existing structures
```

当前代码基础包括：

```text
backend/mdescriptor_studio_backend/analysis/sampling/fps.py
backend/mdescriptor_studio_backend/analysis/sampling/engine.py
frontend/src/pages/analysisShared.tsx
frontend/src/features/analysis/submission.ts
frontend/src/pages/analysisVisualizations.tsx
```

### 2.2 新 Generation

输入：Seed Dataset / Seed structures。  
输出：原数据集中不存在的新结构。

```text
Seed structures
      ↓
Generate / mutate / evolve
      ↓
Geometry validation
      ↓
Descriptor evaluation
      ↓
Novelty / diversity objective
      ↓
Archive update
      ↓
New structures
```

因此禁止简单增加：

```text
Sampling Method:
FPS
Random
GA
PSO
SSW
```

正确设计应为：

```text
Dataset Sampling
  └─ Representative Sampling

Dataset Expansion
  ├─ Descriptor-guided Random Expansion
  ├─ Iterative Maximin Expansion
  ├─ Genetic Algorithm
  ├─ Particle Swarm Optimization
  └─ External Generator Adapter
```

---

## 3. 当前代码基础与可复用能力

当前仓库已经具备实现该功能的大部分基础设施。

### 3.1 可以直接复用

- JobService：后台长任务、状态、进度、取消。
- Descriptor Adapter：结构批处理与 MDescriptor 调用。
- Dataset Adapter：读取 extxyz / DeepMD 等数据。
- Analysis Artifact：结果目录、manifest、metadata、named arrays。
- Dataset lineage：新数据集与父数据集之间的来源关系。
- FPS：max-min 距离、warm start、coverage radius、coverage R²。
- Feature scaling：raw / standardized / robust。
- Plotly：Analysis 页面已有 descriptor-space 可视化。
- `job_runner.py::_perturb_frame()`：已有 jitter / strain 的结构变换逻辑。
- `job_runner.py::_computed_structure_values()`：已有 descriptor 重计算与 row_offsets 处理经验。

### 3.2 必须重构后再复用

当前 `_computed_structure_values()` 会把 atom-level descriptor 平均到 structure-level。该行为适合 perturbation sensitivity，但不适合局域环境发现。

Generation 必须保留：

```text
atomic descriptor matrix
row_offsets
structure descriptor matrix
```

不能把局域环境信息不可逆平均掉。

### 3.3 当前架构中的风险点

根据现有代码结构，新功能需要避免再次产生：

- 前后端重复维护算法词汇表。
- 同一算法出现多个 alias 并进入不同 cache identity。
- AnalysisRegistry 的 category 分支继续膨胀。
- Generation 参数继续塞入已有 `AnalysisParams`。
- 长任务产生半成品 artifact。
- descriptor feature-space signature 不记录导致结果不可复现。

因此 Generation 应作为新的一级能力实现。

---

# 4. 总体架构

推荐新增目录：

```text
backend/mdescriptor_studio_backend/generation/
│
├── __init__.py
├── models.py
├── registry.py
├── engine.py
├── service.py
├── evaluator.py
├── archive.py
├── distance.py
│
├── objectives/
│   ├── __init__.py
│   ├── base.py
│   ├── novelty.py
│   ├── local_diversity.py
│   ├── coverage.py
│   ├── target_region.py
│   └── composite.py
│
├── optimizers/
│   ├── __init__.py
│   ├── base.py
│   ├── random_search.py
│   ├── maximin.py
│   ├── genetic.py
│   ├── pso.py
│   └── external.py
│
├── operators/
│   ├── __init__.py
│   ├── base.py
│   ├── displacement.py
│   ├── strain.py
│   ├── shear.py
│   ├── species_swap.py
│   └── crossover.py
│
├── constraints/
│   ├── __init__.py
│   ├── base.py
│   ├── geometry.py
│   ├── cell.py
│   └── composition.py
│
└── artifacts/
    ├── __init__.py
    ├── writer.py
    └── reader.py
```

前端建议新增：

```text
frontend/src/features/generation/
├── types.ts
├── defaults.ts
├── submission.ts
├── validation.ts
├── store.ts
├── registry.ts
└── resultTypes.ts

frontend/src/pages/
├── Generation.tsx
├── GenerationControls.tsx
├── GenerationResults.tsx
├── generationVisualizations.tsx
└── generationMethodGuides.ts
```

---

# 5. 公共核心数据模型

## 5.1 StructureCandidate

```python
@dataclass
class StructureCandidate:
    candidate_id: str
    atomic_numbers: np.ndarray
    positions: np.ndarray
    cell: np.ndarray
    pbc: np.ndarray

    parent_frame: int | None
    parent_candidate_id: str | None

    generation: int
    operator: str
    operator_params: dict[str, Any]

    metadata: dict[str, Any]
```

原则：

- 不依赖 GUI。
- 不依赖 Analysis 数据结构。
- 不直接持久化大 descriptor 数组。
- candidate ID 在一次 generation run 中唯一。

## 5.2 DescriptorEvaluation

```python
@dataclass
class DescriptorEvaluation:
    structure_values: np.ndarray
    atomic_values: np.ndarray | None
    row_offsets: np.ndarray | None
```

必须满足：

```text
structure_values.shape[0] == n_structures
```

若存在 atom-level 输出：

```text
row_offsets.shape[0] == n_structures + 1
row_offsets[-1] == atomic_values.shape[0]
```

## 5.3 CandidateEvaluation

```python
@dataclass
class CandidateEvaluation:
    candidate_id: str

    valid: bool
    rejection_reasons: tuple[str, ...]

    structure_descriptor: np.ndarray | None
    atomic_descriptors: np.ndarray | None

    structure_novelty: float | None
    local_novelty: float | None
    novel_environment_count: int | None
    coverage_gain: float | None

    penalty: float
    fitness: float | None

    energy: float | None = None
    force_max: float | None = None
```

## 5.4 ObjectiveResult

```python
@dataclass(frozen=True)
class ObjectiveResult:
    score: float
    components: dict[str, float]
```

禁止只返回无解释的单个 float。

## 5.5 ConstraintResult

```python
@dataclass(frozen=True)
class ConstraintResult:
    valid: bool
    penalty: float
    reasons: tuple[str, ...]
```

---

# 6. 分阶段实施总览

| 阶段 | 名称 | 核心成果 | 是否产生新结构 |
|---|---|---|---|
| G0 | Foundation Refactor | 抽离公共结构变换、descriptor evaluator、distance API | 否 |
| G1 | Random Expansion MVP | 随机扰动 + structure novelty + geometry constraints | 是 |
| G2 | Local Environment Expansion | atom-level archive + local novelty | 是 |
| G3 | Iterative Maximin Expansion | batch diversity + FPS/maximin + convergence | 是 |
| G4 | Genetic Algorithm | population/selection/mutation + benchmark | 是 |
| G5 | PSO & External Generator | PSO + external adapter + SSW/LASP 接口 | 是 |

严格依赖：

```text
G0
 ↓
G1
 ↓
G2
 ↓
G3
 ↓
G4
 ↓
G5
```

任何阶段未通过验收，不进入下一阶段。

---

# Phase G0 — Foundation Refactor

## G0.1 阶段目标

在不改变现有用户行为的前提下，把 Generation 会复用的基础能力从 Analysis/JobRunner 中抽离出来。

本阶段不新增可见 Generation 功能。

目标是：

1. 结构变换从 `job_runner.py` 抽离。
2. descriptor 重计算从 `job_runner.py` 抽离。
3. atom row_offsets 不再被公共 evaluator 丢失。
4. FPS 距离计算形成可复用 API。
5. 现有 Analysis 行为完全保持一致。

---

## G0.2 后端任务

### G0-B01：建立 generation package 骨架

新增：

```text
backend/mdescriptor_studio_backend/generation/__init__.py
backend/mdescriptor_studio_backend/generation/models.py
backend/mdescriptor_studio_backend/generation/distance.py
backend/mdescriptor_studio_backend/generation/evaluator.py
backend/mdescriptor_studio_backend/generation/operators/
backend/mdescriptor_studio_backend/generation/constraints/
```

验收：import generation 不产生循环依赖。

### G0-B02：抽离 Atomic Jitter

从：

```text
services/job_runner.py::_perturb_frame()
```

抽到：

```text
generation/operators/displacement.py
```

接口：

```python
def atomic_displacement(frame, vector, amplitude): ...
```

要求：

- 不改变原结构对象。
- dtype 明确为 float64。
- PBC / cell 原样保留。
- amplitude=0 时数值等价于原结构。

### G0-B03：抽离 strain

新增：

```python
def isotropic_strain(frame, strain): ...
```

必须保持 fractional coordinates 不变。

测试：

```text
fractional_before ≈ fractional_after
cell_after = cell_before * (1 + strain)
```

### G0-B04：新增 DescriptorEvaluator

文件：

```text
generation/evaluator.py
```

接口：

```python
class DescriptorEvaluator:
    def evaluate_structures(
        self,
        structures,
        *,
        preserve_atomic: bool = False,
        control=None,
    ) -> DescriptorEvaluation:
        ...
```

功能：

1. `adapter.to_structure_batch()`。
2. `adapter.compute()`。
3. 检查 finite values。
4. 验证 `row_offsets`。
5. 可选择 structure pooling。
6. 可保留 atom-level values。

### G0-B05：明确 pooling strategy

不要把平均池化写死。

新增：

```python
StructurePooling = Literal["native", "mean", "sum"]
```

Generation 默认：

```text
native structure output if available
otherwise mean pooling for structure-level metrics
and keep atomic output independently
```

### G0-B06：抽离 blocked nearest-distance

从 FPS 的内部逻辑提取：

```text
generation/distance.py
```

建议接口：

```python
def min_sqdist_to_reference(
    query: np.ndarray,
    reference: np.ndarray,
    *,
    block_size: int = 2048,
) -> np.ndarray:
    ...
```

要求：

- 不构造完整 N×M 矩阵。
- float64。
- 与 `scipy.spatial.distance.cdist(..., sqeuclidean)` 对齐。
- 完全重复行距离严格为 0。

然后 FPS 改为复用该函数。

### G0-B07：避免双实现

`job_runner.py::_run_perturbation_sensitivity()` 改为调用：

```text
Structure operator
DescriptorEvaluator
```

删除重复计算逻辑。

---

## G0.3 测试

新增：

```text
tests/test_generation_distance.py
tests/test_generation_evaluator.py
tests/test_generation_operators.py
```

必须覆盖：

- jitter amplitude=0。
- jitter 固定 seed 可复现。
- strain fractional coordinate invariant。
- descriptor batch shape。
- atom row_offsets preservation。
- mean pooling correctness。
- exact duplicate distance=0。
- blocked distance 与 brute force 一致。
- NaN / Inf 正确失败。

同时全部现有测试必须通过。

---

## G0.4 验收条件

- [ ] `job_runner.py` 不再拥有具体 jitter / strain 数学实现。
- [ ] 公共 evaluator 可返回 atom values + row_offsets。
- [ ] FPS 使用公共 distance primitive。
- [ ] Perturbation Sensitivity 输出与重构前数值一致。
- [ ] 所有现有 Analysis E2E 测试通过。
- [ ] 无新的前后端 public API。

---

# Phase G1 — Descriptor-Guided Random Expansion MVP

## G1.1 阶段目标

实现第一版真正能够生成新结构的功能：

```text
Seed structure
 ↓
Random perturbation
 ↓
Geometry filter
 ↓
Descriptor evaluation
 ↓
Structure novelty
 ↓
Accept / reject
```

只做 structure-level novelty，不做 GA，不做 PSO。

---

## G1.2 数据库

新增 Migration 12。

### generation_runs

```sql
CREATE TABLE generation_runs (
    id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    descriptor_run_id TEXT,

    optimizer TEXT NOT NULL,
    objective TEXT NOT NULL,
    params_json TEXT NOT NULL,

    status TEXT NOT NULL,

    evaluations INTEGER NOT NULL DEFAULT 0,
    accepted_count INTEGER NOT NULL DEFAULT 0,

    result_path TEXT,
    artifact_manifest_json TEXT,
    preview_json TEXT,
    warnings_json TEXT,

    cache_key TEXT,
    stale_reason TEXT,

    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT
);

CREATE INDEX idx_generation_dataset
ON generation_runs(dataset_id, created_at);

CREATE INDEX idx_generation_status
ON generation_runs(status, created_at);
```

Jobs 新增：

```sql
ALTER TABLE jobs ADD COLUMN generation_run_id TEXT;
```

### 数据库原则

禁止将下列内容逐行放入 SQLite：

- atomic positions。
- descriptor vectors。
- atom-level environment descriptors。
- 每个 candidate 的完整 metadata。

全部存 artifact。

---

## G1.3 GenerationService

新增：

```text
generation/service.py
```

核心 API：

```python
submit(params) -> {job_id, generation_id}
get(generation_id)
list(dataset_id=None)
preview(generation_id)
materialize(generation_id, ...)
```

GenerationService 负责：

- 参数校验。
- dataset / descriptor run freshness。
- cache identity。
- generation_runs 状态。
- JobService 提交。
- artifact commit。

GenerationEngine 负责算法，不直接写数据库。

---

## G1.4 RPC

新增：

```text
generation.catalog
generation.submit
generation.get
generation.list
generation.preview
generation.materialize
generation.export
```

取消仍复用：

```text
job.cancel
```

不要增加 `generation.cancel` 的重复语义，除非 UI/API 一致性确实需要 wrapper。

---

## G1.5 GenerationRegistry

新增独立 registry：

```python
GenerationRegistry
OptimizerSpec
ObjectiveSpec
OperatorSpec
ConstraintSpec
```

GenerationRegistry 是 generation vocabulary 的唯一 backend owner。

MVP optimizer：

```text
random
```

MVP objective：

```text
structure_novelty
```

MVP operators：

```text
atomic_displacement
isotropic_strain
anisotropic_strain
```

MVP constraints：

```text
finite_geometry
valid_cell
minimum_distance
maximum_strain
maximum_displacement
composition_lock
```

---

## G1.6 GeometryConstraint

### Minimum distance

支持：

```text
absolute
covalent_radius
```

Covalent mode：

\[
r_{ij} > \alpha(r_i^{cov} + r_j^{cov})
\]

UI 初始默认建议：

```text
mode = covalent_radius
factor = 0.70
```

但该值必须明确为用户可配置工程默认，不宣传为通用物理常数。

### Cell

拒绝：

```text
|det(cell)| < epsilon
non-finite cell
volume change > configured bound
```

### Composition

MVP：

```text
composition locked
atom count locked
```

---

## G1.7 StructureNoveltyObjective

已有 reference descriptor matrix：

\[
D=\{z_1,z_2,...,z_M\}
\]

候选：

\[
z(X)
\]

定义：

\[
N(X)=\min_{z_j\in D}\|\tilde z(X)-\tilde z_j\|_2
\]

### Scaling

默认 robust scaling。

Scaler 必须：

1. 只由 reference dataset 拟合。
2. generation run 开始时冻结。
3. 整个 run 不重新拟合。
4. 参数写入 artifact metadata。

禁止每代重新拟合，否则不同 generation 的 fitness 不可比较。

---

## G1.8 Random optimizer

配置：

```python
@dataclass
class RandomOptimizerConfig:
    candidate_batch_size: int = 64
    accepted_per_round: int = 8
```

MVP 算法：

```python
while not stop:
    seeds = select_seed_structures()
    candidates = mutate(seeds)
    candidates = geometry_filter(candidates)
    descriptors = evaluate_batch(candidates)
    scores = structure_novelty(descriptors, archive)
    accepted = take_best(scores)
    archive.add(accepted)
```

### 重要限制

G1 可以先只按照 novelty 排序，但必须在代码中预留 batch diversity selection 接口，G3 再实现。

---

## G1.9 StopCriteria

MVP 支持：

```text
max_evaluations
max_accepted
max_rounds
min_novelty
```

优先级：

```text
hard budget > user target > convergence
```

每次 descriptor evaluation 必须精确计数。

---

## G1.10 Artifact

建议目录：

```text
generation/gen_<id>/
├── manifest.json
├── metadata.json
├── accepted.extxyz
├── candidates.jsonl
├── fitness.npy
├── novelty.npy
├── generation.npy
├── parent_index.npy
├── structure_descriptors.npy
└── convergence.json
```

`accepted.extxyz` 每个 frame 写：

```text
generation_run_id
candidate_id
parent_frame
round
operator
fitness
novelty
```

---

## G1.11 Materialize

`generation.materialize`：

```text
Accepted structures
      ↓
new extxyz dataset
      ↓
dataset.register
      ↓
dataset_lineage
```

Lineage 建议：

```text
operation = dataset_generation
```

具体 optimizer/objective 写 metadata，不写成越来越多 lineage operation 名称。

原则：源 Dataset 永不修改。

---

## G1.12 前端

新增一级页面：

```text
Generation
```

### MVP 页面分区

#### SOURCE

- Dataset。
- Descriptor run。
- Seed scope。

#### SEARCH OBJECTIVE

MVP：

```text
Structure novelty
Feature scaling: Robust / Standardized / Raw
Minimum accepted novelty
```

#### SEARCH SPACE

```text
Atomic displacement
Isotropic strain
Anisotropic strain
```

#### PHYSICAL CONSTRAINTS

```text
Minimum distance
Maximum strain
Maximum displacement
Maximum volume change
Composition lock
```

#### OPTIMIZER

```text
Descriptor-guided Random Expansion
Candidate batch
Accepted per round
```

#### COMPUTE BUDGET

```text
Max descriptor evaluations
Max accepted structures
Random seed
```

---

## G1.13 运行结果

实时 metrics：

```text
Evaluations
Valid candidates
Accepted structures
Geometry rejected
Low-novelty rejected
Best novelty
Mean accepted novelty
```

实时曲线：

```text
Best novelty vs Descriptor evaluations
Accepted structures vs Descriptor evaluations
```

---

## G1.14 测试

新增：

```text
tests/test_generation_service.py
tests/test_generation_random.py
tests/test_generation_constraints.py
tests/test_generation_artifact.py
frontend/e2e/generation-random.spec.ts
```

固定 seed 必须保证：

```text
same dataset
same descriptor run
same params
same seed
=> same accepted candidate sequence
```

---

## G1.15 验收条件

- [ ] 可以从 Dataset 产生真实新结构。
- [ ] 新结构不修改源文件。
- [ ] 非法几何在 descriptor 计算前被拒绝。
- [ ] descriptor evaluation 使用 batch。
- [ ] reference scaler 在 run 内冻结。
- [ ] generation job 可取消。
- [ ] 取消不留下 COMPLETED artifact。
- [ ] accepted.extxyz 可重新注册为 Dataset。
- [ ] dataset lineage 正确。
- [ ] 固定 seed 完全可复现。

---

# Phase G2 — Local Environment Expansion

## G2.1 阶段目标

把优化目标从 structure-level diversity 扩展到 atom/local-environment diversity。

这是该功能最重要的科学阶段。

---

## G2.2 LocalEnvironmentArchive

新增：

```python
class LocalEnvironmentArchive:
    ...
```

存储：

```text
reference atomic descriptors
accepted atomic descriptors
optional element labels
structure mapping
```

至少支持：

```python
nearest(query_atomic_descriptors)
novel_count(query, threshold)
add(query)
```

---

## G2.3 元素处理策略

默认局域环境距离只在相同 central element 内比较。

例如 Si 原子 environment 不直接与 O central environment 做 nearest distance。

Archive 建议：

```text
archive_by_element[Z]
```

目的：避免不同元素 descriptor 天然分离导致“假 novelty”。

如果某 descriptor 已经按元素形成独立 feature layout，也仍建议保留 central-element filtering 作为显式策略。

UI：

```text
Environment matching:
Same central element [default]
All environments [advanced]
```

---

## G2.4 Local novelty

对候选结构 X 的每个 atom descriptor：

\[
d_i=\min_{a\in A}\|z_i-a\|
\]

实现三种 aggregation：

```text
mean
top_fraction_mean
quantile
```

默认：

```text
top_fraction_mean
top_fraction = 0.20
```

即：

\[
F_{local}=\operatorname{Mean}(\operatorname{Top}_{20\%}(d_i))
\]

同时记录：

```text
mean_local_novelty
max_local_novelty
q90_local_novelty
top_fraction_mean
```

最大值只用于诊断，不作为默认优化目标。

---

## G2.5 Novel environment count

给定 threshold：

\[
N_{new}=\sum_i I(d_i>d_{threshold})
\]

同时记录：

\[
f_{new}=N_{new}/N_{atoms}
\]

结果 UI：

```text
Atoms                       128
Novel environments           23
Novel fraction             18.0%
Mean novelty                0.42
Top-20% novelty             1.38
Maximum novelty             2.72
```

---

## G2.6 Objective preset

新增：

```text
Local Environment Diversity
```

建议第一版 objective：

\[
F(X)=F_{top20}(X)
\]

不要马上引入复杂多目标权重。

第二个可选 preset：

\[
F(X)=F_{top20}(X)+\lambda f_{new}(X)
\]

其中 lambda 默认保持简单且写入参数。

---

## G2.7 Artifact 扩展

新增：

```text
local_environment_descriptors.npy
local_row_offsets.npy
local_novelty.npy
novel_environment_count.npy
central_elements.npy
```

对于大型 archive：

- 可使用 memmap。
- preview 不通过 IPC 返回完整 atom descriptor。

---

## G2.8 前端结果

新增：

```text
LOCAL ENVIRONMENT DISCOVERY
```

显示：

```text
Original local environments
Evaluated local environments
Accepted local environments
Novel local environments
Novel fraction
```

Descriptor-space visualization：

```text
Original environments
Generated environments
Accepted novel environments
```

可选 PCA 仅用于视觉投影，fitness 仍在完整 scaled descriptor space 中计算。

---

## G2.9 Structure ↔ Atom 映射

必须保存：

```text
candidate_id
atom_index
central_element
parent_frame
```

点击一个 novel environment：

```text
PCA point
 ↓
Candidate structure
 ↓
Atom index
 ↓
Explore / 3Dmol highlight
```

这是 UI 的重要差异化功能。

---

## G2.10 测试

构造解析测试：

Reference：

```text
A = {0, 1, 2, 3}
```

候选：

```text
X1 = {0.1, 1.1, 2.1, 3.1}
X2 = {0.1, 1.1, 8.0, 9.0}
```

必须满足：

```text
local_novelty(X2) > local_novelty(X1)
novel_count(X2) > novel_count(X1)
```

再构造：

```text
X3 = {0.1, 1.1, 2.1, 1000}
```

用于验证：

- max 极易被异常值支配。
- top_fraction_mean 行为符合设计。
- geometry constraint 在真实结构路径中阻止明显异常几何。

---

## G2.11 验收条件

- [ ] atom-level descriptor 不被平均丢失。
- [ ] row_offsets 全链路保存。
- [ ] 可按 central element 构建 archive。
- [ ] local novelty 数值与 brute-force 基准一致。
- [ ] 能统计 novel environment count。
- [ ] UI 可以从 novel point 跳回具体 structure + atom。
- [ ] 大型 local descriptor 不直接通过 IPC 全量传输。

---

# Phase G3 — Iterative Maximin Expansion

## G3.1 阶段目标

解决 Random Expansion 的核心问题：

> 同一 batch 中多个高 novelty candidate 可能彼此高度相似。

引入 batch diversity / maximin selection，使接受的结构不仅远离旧 Archive，而且彼此也尽量不同。

---

## G3.2 Candidate diversity

当前 Archive A，候选集合 C。

每个候选对旧 archive 的 novelty：

\[
d_A(x)=\min_{a\in A}\|x-a\|
\]

接受集合 S 逐步建立。

选择时再计算：

\[
d_S(x)=\min_{s\in S}\|x-s\|
\]

目标可以采用：

\[
score(x)=\min(d_A(x),d_S(x))
\]

这就是 warm-start maximin/FPS 思路。

推荐优先使用该形式，而不是手工线性权重。

---

## G3.3 MaximinSelector

新增：

```python
class MaximinSelector:
    def select(
        candidate_features,
        archive_features,
        n_select,
        min_distance=0.0,
    ) -> SelectionResult:
        ...
```

尽量复用 FPS 公共实现。

必须返回：

```text
selected_indices
selection_distances
nearest_archive_distances
```

---

## G3.4 新 optimizer

新增：

```text
iterative_maximin
```

注意：它不是传统 optimization algorithm，而是：

```text
random candidate generation
+
maximin batch selection
```

因此内部应组合：

```text
CandidateGenerator
MaximinSelector
```

而不是复制 Random optimizer 全部代码。

---

## G3.5 Coverage/convergence metrics

新增每轮：

```text
best_novelty
mean_accepted_novelty
median_accepted_novelty
accepted_per_100_evaluations
novel_environments_per_100_evaluations
```

如果有 reference pool，再计算：

```text
coverage_radius
coverage_mean
coverage_r2
```

---

## G3.6 Saturation stopping

新增：

```text
no_improvement_rounds
minimum_discovery_rate
```

例如：

```text
连续 10 轮，每 100 次 descriptor evaluation 新增局域环境 < 1
=> stop
```

内部定义：

\[
R_{discover}=\frac{\Delta N_{new}}{\Delta N_{eval}}
\]

必须在 artifact 中记录停止原因：

```text
max_evaluations
max_accepted
max_rounds
minimum_novelty
saturation
cancelled
```

---

## G3.7 UI

Optimizer：

```text
Iterative Maximin Expansion
```

参数：

```text
Candidate batch       128
Accept per round        8
Min descriptor distance
No-improvement rounds  10
```

结果新增曲线：

```text
Novel environments vs evaluations
Mean accepted novelty vs evaluations
Discovery rate vs evaluations
```

---

## G3.8 Benchmark Gate

G3 完成后必须进行第一次正式 benchmark：

固定：

```text
same dataset
same descriptor
same operators
same geometry constraints
same random seeds set
same descriptor evaluation budget
```

比较：

```text
Random Expansion
Iterative Maximin Expansion
```

指标：

```text
accepted structures
mean novelty
q90 novelty
novel environment count
novel environments / 1000 evaluations
coverage improvement if reference pool exists
```

如果 Maximin 没有稳定优于 Random，需要先分析原因，不进入 GA。

---

## G3.9 验收条件

- [ ] batch 中不会大量接受 near-duplicate candidate。
- [ ] maximin selection 与 brute force FPS 基准一致。
- [ ] stopping reason 明确记录。
- [ ] convergence curves 可复现。
- [ ] 固定 budget 下 Maximin 至少在一个明确指标上稳定优于 Random。

---

# Phase G4 — Genetic Algorithm

## G4.1 阶段目标

在已经验证 objective、constraint、archive 与 batch selection 正确后，引入 evolutionary optimizer。

第一版 GA 不追求完整晶体结构预测能力，而聚焦：

> 在已有物理结构附近，学习哪些扰动组合能更高效地产生新的 descriptor/local-environment 区域。

---

## G4.2 Genome 设计

GA v1 禁止直接把完整 Cartesian 坐标作为 genome。

使用 perturbation genome：

```python
@dataclass
class PerturbationGenome:
    parent_frame: int

    displacement_sigma: float

    strain_x: float
    strain_y: float
    strain_z: float

    shear_xy: float
    shear_xz: float
    shear_yz: float

    random_seed: int
```

Decoder：

```text
Genome
 ↓
load parent
 ↓
apply displacement
 ↓
apply strain/shear
 ↓
Candidate
```

优势：

- 不改变 atom count。
- 不改变 composition。
- 避免原子排序 crossover 难题。
- 搜索空间可解释。
- 与现有 perturbation 代码兼容。

---

## G4.3 GA v1 算法

第一版采用：

```text
elitism
+
tournament selection
+
mutation
```

先不做 crossover。

默认建议：

```text
Population         64
Elite               8
Tournament size     3
Mutation rate      0.20
Generations        50
```

但 compute budget 仍以：

```text
max_descriptor_evaluations
```

作为最终硬上限。

---

## G4.4 GA 执行流程

```python
population = initialize_population()

while not stop:
    candidates = decode(population)
    valid = constraints.filter(candidates)
    descriptors = evaluator.evaluate_batch(valid)
    fitness = objective.evaluate_batch(descriptors, archive)

    accepted = archive_selector.select(...)
    archive.add(accepted)

    elites = select_elites(population, fitness)

    offspring = []
    while len(offspring) < population_size - len(elites):
        parent = tournament(population, fitness)
        child = mutate(parent)
        offspring.append(child)

    population = elites + offspring
```

### Population diversity

必须记录：

```text
genome diversity
descriptor diversity
```

避免 GA 看似 fitness 提升，实际 population collapse。

---

## G4.5 Mutation operators

至少：

```text
parent switch
sigma perturbation
strain perturbation
shear perturbation
seed perturbation
```

所有 mutation 必须尊重 search-space bounds。

---

## G4.6 GA v2：Crossover Gate

只有当 GA v1 在固定 descriptor evaluation budget 下稳定优于 Maximin，才实现 crossover。

PerturbationGenome crossover 可以使用：

```text
uniform crossover
arithmetic crossover
```

例如连续参数：

\[
child=\alpha p_1+(1-\alpha)p_2
\]

parent_frame 不做简单数值插值，只能从父本之一继承。

---

## G4.7 GA benchmark

固定预算：

```text
10,000 descriptor evaluations
```

至少 5 个随机 seed。

比较：

```text
Random
Iterative Maximin
GA v1
```

报告：

```text
mean ± std
median
best
```

主要指标不要只用 best fitness。

必须包括：

```text
novel environments / 1000 evaluations
unique accepted structures / 1000 evaluations
mean accepted local novelty
coverage gain if available
geometry rejection rate
```

---

## G4.8 前端

新增 optimizer：

```text
Genetic Algorithm
```

参数：

```text
Population size
Elite size
Tournament size
Mutation rate
Generations
```

Advanced：

```text
Mutation scale
Parent reselection rate
Population restart threshold
```

实时：

```text
Generation
Evaluations
Best fitness
Median fitness
Population diversity
Accepted archive size
```

图：

```text
Best / median fitness vs generation
Population descriptor diversity vs generation
Novel environments vs evaluations
```

---

## G4.9 NSGA-II：可选 G4.5

只有在存在至少两个真正独立科学目标时加入。

建议目标：

```text
maximize local novelty
maximize structure diversity
minimize energy (future)
```

不要过早把多个量纲直接线性加权。

若加入 NSGA-II：

- 保存 Pareto rank。
- 保存 crowding distance。
- UI 展示 Pareto front。
- 不自动替用户选择“最佳”结构。

---

## G4.10 验收条件

- [ ] GA 固定 seed 可完全复现。
- [ ] population 无非法 genome。
- [ ] mutation bounds 全部受控。
- [ ] descriptor budget 精确计数。
- [ ] cancellation 在 generation boundary 内及时响应。
- [ ] GA benchmark 使用相同预算公平比较。
- [ ] 只有数据证明有效时才进入 crossover/NSGA-II。

---

# Phase G5 — PSO & External Generator

## G5.1 阶段目标

在 Generation 框架成熟后，增加第二种连续优化器与外部结构搜索接口。

本阶段拆成 G5A / G5B。

---

# G5A — Particle Swarm Optimization

## G5A.1 适用范围

PSO v1 限定：

```text
fixed composition
fixed atom count
fixed parent structure topology
continuous perturbation parameters
```

不要第一版就允许：

```text
vacancy/interstitial
composition mutation
variable atom count
```

---

## G5A.2 Particle representation

推荐仍优化 perturbation vector，而不是所有 Cartesian 坐标。

```text
Δ displacement parameters
strain components
shear components
```

若后续验证需要，再增加 direct-coordinate PSO。

---

## G5A.3 更新公式

\[
v_i^{t+1}=\omega v_i^t+c_1r_1(p_i-x_i^t)+c_2r_2(g-x_i^t)
\]

\[
x_i^{t+1}=x_i^t+v_i^{t+1}
\]

参数：

```text
particles
inertia ω
cognitive c1
social c2
velocity clamp
```

---

## G5A.4 Random immigrants

为防止全部粒子收缩到同一 descriptor basin，加入：

```text
random immigrant fraction = 10–20%
```

当 swarm diversity 低于阈值时触发。

---

## G5A.5 Benchmark

同一 descriptor evaluation budget 比较：

```text
Random
Maximin
GA
PSO
```

不要比较 generation 数，因为每个 optimizer 每代 evaluation 数不同。

---

# G5B — External Generator Adapter

## G5B.1 目标

不要在 MDescriptorStudio 内部重新实现完整 SSW/USPEX/CALYPSO。

新增通用外部结构生成协议：

```python
class ExternalGeneratorAdapter(Protocol):
    name: str

    def prepare(self, request, workdir): ...
    def command(self, request, workdir) -> list[str]: ...
    def collect(self, workdir) -> list[StructureCandidate]: ...
```

---

## G5B.2 数据流

```text
MDescriptorStudio
 ↓
write input structures/config
 ↓
external command
 ↓
SSW / LASP / AIRSS / other engine
 ↓
collect structures
 ↓
geometry constraints
 ↓
MDescriptor evaluation
 ↓
objective/archive
```

---

## G5B.3 安全限制

External command：

- 不接受任意 shell string 拼接。
- 使用 argv list。
- workdir 隔离。
- stdout/stderr 落 run log。
- 支持取消并终止子进程树。
- 输出结构数量设置上限。
- 解析失败不能污染 artifact。

---

## G5B.4 SSW 的产品定位

UI 中不要把内部随机 displacement 冒充 SSW。

只有真正通过外部 SSW engine/PES workflow 接入后才显示：

```text
External SSW
```

否则内部算法统一使用：

```text
Descriptor-guided Random Expansion
Iterative Maximin Expansion
```

---

## G5 验收条件

- [ ] PSO 与其他 optimizer 共用同一 Objective/Constraint/Evaluator/Archive。
- [ ] PSO 不复制 GA/Random 的 descriptor pipeline。
- [ ] External adapter 无 shell injection 风险。
- [ ] 外部任务可取消。
- [ ] 外部输出经过相同 geometry constraint 和 descriptor objective。
- [ ] SSW 名称只用于真正 SSW backend。

---

# 7. GenerationConfig 设计

前端和后端建议共享相同概念结构。

```typescript
interface GenerationConfig {
  source: SourceConfig;
  representation: RepresentationConfig;
  objective: ObjectiveConfig;
  searchSpace: SearchSpaceConfig;
  constraints: ConstraintConfig;
  optimizer: OptimizerConfig;
  budget: BudgetConfig;
  reproducibility: ReproducibilityConfig;
}
```

Optimizer 必须使用 discriminated union：

```typescript
type OptimizerConfig =
  | {
      type: "random";
      batchSize: number;
      acceptedPerRound: number;
    }
  | {
      type: "maximin";
      batchSize: number;
      acceptedPerRound: number;
      minDistance: number;
    }
  | {
      type: "genetic";
      populationSize: number;
      eliteSize: number;
      tournamentSize: number;
      mutationRate: number;
    }
  | {
      type: "pso";
      particles: number;
      inertia: number;
      cognitive: number;
      social: number;
    };
```

禁止：

```typescript
interface GenerationConfig {
  gaPopulation?: number;
  psoParticles?: number;
  ...
}
```

因为这种结构会产生大量非法状态。

---

# 8. RPC 参数建议

示例：

```json
{
  "dataset_id": "ds_xxx",
  "descriptor_run_id": "run_xxx",

  "representation": {
    "mode": "local_environment",
    "scaling": "robust",
    "element_matching": "same_central_element"
  },

  "objective": {
    "type": "local_environment_novelty",
    "aggregation": "top_fraction_mean",
    "top_fraction": 0.2,
    "novelty_threshold": 0.25
  },

  "search_space": {
    "atomic_displacement": {
      "enabled": true,
      "max_sigma": 0.15
    },
    "isotropic_strain": {
      "enabled": true,
      "max_abs": 0.05
    }
  },

  "constraints": {
    "min_distance": {
      "mode": "covalent_radius",
      "factor": 0.7
    },
    "max_volume_change": 0.2,
    "lock_composition": true,
    "lock_atom_count": true
  },

  "optimizer": {
    "type": "maximin",
    "batch_size": 128,
    "accepted_per_round": 8
  },

  "budget": {
    "max_evaluations": 10000,
    "max_accepted": 500,
    "max_rounds": 200
  },

  "seed": 42
}
```

---

# 9. Cache / Reproducibility

Generation cache identity 必须包含：

```text
dataset fingerprint
descriptor feature-space signature
descriptor parameters
optimizer config
objective config
search-space config
constraints
budget
seed
algorithm version
```

### 固定 seed

允许 cache / deterministic replay。

### Random seed mode

如果用户选择随机 seed：

```text
generate concrete seed at submission
record concrete seed in generation_runs
```

之后仍可复现。

不要让 artifact 只写：

```text
seed = random
```

必须保存实际 seed。

---

# 10. Descriptor feature-space metadata

每个 generation run 必须记录：

```text
descriptor_name
descriptor_version
engine_version
descriptor_parameters
feature_count
row_semantics
feature_space_signature
scaling_mode
scaling_center/scaling_scale or equivalent scaler metadata
```

否则未来 descriptor 参数变化时，旧 Generation Run 无法科学解释。

---

# 11. Artifact 原子性

使用现有 Analysis artifact 设计原则：

```text
work directory
 ↓
write all arrays/metadata
 ↓
fsync/validate
 ↓
atomic commit/rename
 ↓
DB status COMPLETED
```

任何失败或取消：

```text
status != COMPLETED
=> committed artifact must not remain
```

特别测试：

- descriptor compute 中取消。
- candidate generation 中取消。
- artifact write 中断。
- materialize 中断。

---

# 12. 性能策略

## 12.1 Descriptor compute 是主成本

优先优化：

```text
batch candidate generation
batch descriptor computation
batch nearest-distance query
```

不要优先微优化 GA Python 循环。

## 12.2 禁止逐结构 compute

优先：

```python
evaluator.evaluate_structures(batch_of_128)
```

避免：

```python
for structure in candidates:
    adapter.compute(one_structure)
```

## 12.3 Batch archive update

正确：

```text
Generate batch
Evaluate batch
Score against frozen archive
Diversity select
Accept batch
Update archive once
```

避免：

```text
candidate → archive.add → candidate → archive.add
```

这样既提高性能，也降低顺序依赖。

---

# 13. 大规模 Local Environment Archive

MVP：exact blocked search。

当 atom environments 达到较大规模后，再抽象：

```python
class NeighborIndex(Protocol):
    def fit(reference): ...
    def nearest(query): ...
    def add(values): ...
```

实现顺序：

```text
ExactBlockedIndex
→ optional HNSW
→ optional FAISS
```

不要在 G2 就引入 FAISS 作为硬依赖。

Exact implementation 作为科学基准必须永久保留。

---

# 14. 前端导航建议

推荐最终导航：

```text
Dataset
Explore
Descriptor
Analysis
Generation
Results
```

Generation 不放在 Analysis 内部，因为它：

- 调用 descriptor engine。
- 产生新结构。
- 写 artifact。
- 创建新 Dataset。
- 有独立 history。

已经是一级 scientific workflow。

---

# 15. Generation 页面最终布局

## SOURCE

```text
Dataset
Descriptor run
Seed scope
Structure / Local environment representation
```

## OBJECTIVE

```text
Structure novelty
Local environment novelty
Coverage completion
Target region
Composite / Pareto [later]
```

## SEARCH SPACE

```text
Atomic displacement
Isotropic strain
Anisotropic strain
Cell shear
Species swap [later]
```

## PHYSICAL CONSTRAINTS

```text
Minimum distance
Maximum displacement
Maximum strain
Maximum volume change
Composition lock
Atom-count lock
```

## OPTIMIZER

```text
Random
Iterative Maximin
Genetic Algorithm
PSO
External Generator
```

## COMPUTE BUDGET

```text
Max descriptor evaluations
Max accepted structures
Max rounds/generations
Convergence criterion
Seed
```

---

# 16. 结果页面最终指标

顶部建议固定显示：

```text
Original structures
Candidates generated
Candidates evaluated
Geometry rejected
Duplicate rejected
Low-novelty rejected
Accepted structures
```

Local mode：

```text
Original environments
Generated environments
Novel environments
Novel fraction
```

搜索质量：

```text
Best novelty
Mean accepted novelty
Median accepted novelty
Discovery rate
Coverage radius before/after
Coverage R² before/after
```

---

# 17. 核心可视化

## 17.1 Descriptor-space PCA

仅作为视觉投影：

```text
Original
Generated candidates
Accepted
```

fitness 不使用 PCA 降维空间，除非 objective 明确是 PCA target-region。

## 17.2 Convergence

至少：

```text
Novel environments vs evaluations
Accepted structures vs evaluations
Best/mean novelty vs evaluations
```

GA：

```text
Best/median fitness vs generation
Population diversity vs generation
```

## 17.3 Reject reasons

```text
Geometry
Duplicate
Low novelty
Constraint
Descriptor failure
```

用于快速判断搜索参数是否过激。

---

# 18. Scientific Benchmark 设计

固定 benchmark protocol：

1. 相同 Dataset。
2. 相同 Descriptor Run。
3. 相同 search space。
4. 相同 constraints。
5. 相同 descriptor evaluation budget。
6. 多个随机 seed。
7. 不使用 generation 数作为公平预算。

主指标：

\[
\frac{N_{novel\ environments}}{N_{descriptor\ evaluations}}
\]

建议统一报告：

```text
Novel environments / 1000 evaluations
Unique accepted structures / 1000 evaluations
Mean local novelty
Q90 local novelty
Coverage radius change
Geometry rejection rate
Wall time
Descriptor compute time
Search overhead time
```

其中 wall time 为工程指标，descriptor evaluation efficiency 为主要科学指标。

---

# 19. 基准方法

至少比较：

```text
Random perturbation
Iterative Maximin
Genetic Algorithm
PSO [G5]
```

可选：

```text
MD trajectory + FPS
```

用于回答：

> 主动生成相对于先生成 trajectory 再 FPS，是否能以更少 descriptor evaluations 找到更多新环境？

---

# 20. 测试矩阵

## Unit

```text
operators
constraints
distance
scaling
archives
objectives
optimizer state transition
stop criteria
artifact serialization
```

## Integration

```text
Dataset → DescriptorEvaluator
DescriptorEvaluator → Objective
Optimizer → Archive
GenerationService → JobService
Generation → Artifact
Materialize → DatasetService
```

## E2E

```text
Open dataset
Select descriptor run
Configure generation
Run
Observe progress
Open result
Select candidate
Materialize dataset
Switch to new dataset
Run descriptor
```

## Regression

必须保证：

```text
Analysis Sampling
Perturbation Sensitivity
Descriptor calculation
Dataset registration
Existing artifact loading
```

不发生回归。

---

# 21. CI Gate

每个 phase 合并前：

```text
backend unit tests
frontend unit tests
frontend E2E
ruff/lint/typecheck
artifact roundtrip
fixed-seed reproducibility
```

G2 起增加：

```text
scientific numeric regression fixtures
```

G3 起增加：

```text
small benchmark smoke test
```

正式大 benchmark 不放 CI。

---

# 22. 代码质量约束

## 22.1 单一 vocabulary owner

Generation backend registry 是 optimizer/objective/operator 名称的权威来源。

前端 catalog 优先由：

```text
generation.catalog
```

动态获取。

不要再长期维护独立硬编码表。

## 22.2 不向 AnalysisRegistry 增加 GA/PSO

禁止：

```text
analysis.ga
analysis.pso
analysis.ssw
```

除非未来 architecture 完全统一后另行设计。

## 22.3 不继续膨胀 AnalysisParams

Generation 单独：

```text
GenerationConfig
useGenerationStore
```

## 22.4 Algorithm core 无 GUI 依赖

以下代码禁止 import React/Tauri/UI 概念：

```text
generation/objectives
generation/optimizers
generation/operators
generation/constraints
generation/archive
```

---

# 23. 错误码建议

新增结构化错误，例如：

```text
GENERATION_INPUT_INVALID
GENERATION_DESCRIPTOR_INCOMPATIBLE
GENERATION_NO_VALID_CANDIDATES
GENERATION_CONSTRAINT_FAILURE
GENERATION_EXTERNAL_ENGINE_FAILED
GENERATION_ARTIFACT_INVALID
```

禁止 UI 依赖解析 error message 文本。

---

# 24. Job 文案

建议 jobs store 增加：

```text
generation.random          Descriptor-guided random expansion
generation.maximin         Iterative maximin expansion
generation.genetic         Genetic dataset expansion
generation.pso             Particle-swarm dataset expansion
generation.external        External structure generation
```

进度 message：

```text
generating candidates
validating geometry
computing descriptors
scoring novelty
selecting diverse candidates
updating archive
writing generation artifact
```

---

# 25. 取消粒度

每个长期步骤调用 cancellation checkpoint：

```text
before generation batch
after geometry filter
before descriptor compute
after descriptor compute
inside nearest-distance blocks
before archive update
before artifact commit
```

对于无法协作取消的第三方数值/外部引擎：

```text
subprocess isolation
```

沿用现有 hard-cancel 思路。

---

# 26. Phase 风险与回退策略

## G0 风险

风险：重构改变现有 perturbation sensitivity 数值。  
回退：保留重构前 numeric fixture，必须 bitwise/严格 tolerance 对比。

## G1 风险

风险：大量 candidate 因原子碰撞被拒绝。  
处理：UI 显示 reject rate；默认扰动幅度保守；不自动放宽约束。

## G2 风险

风险：local archive 内存快速增长。  
处理：memmap + exact blocked index；暂不全量 IPC。

## G3 风险

风险：Maximin 算法成本随 archive 增长。  
处理：block distance；后续 NeighborIndex abstraction。

## G4 风险

风险：GA 未优于 Random/Maximin。  
处理：以 benchmark 作为 feature gate；无优势则保留 experimental，不继续复杂化。

## G5 风险

风险：PSO population collapse / external engine 不稳定。  
处理：random immigrants；外部 engine 隔离；输出统一再过 constraint。

---

# 27. 建议的 Git 分支/PR 拆分

不要一个 PR 实现整套 Generation。

建议：

```text
PR-G0-01 generation package + models
PR-G0-02 operator extraction
PR-G0-03 descriptor evaluator
PR-G0-04 shared distance primitive

PR-G1-01 database + service + RPC
PR-G1-02 constraints
PR-G1-03 random optimizer + objective
PR-G1-04 artifact + materialize
PR-G1-05 Generation UI MVP

PR-G2-01 atomic descriptor preservation
PR-G2-02 local archive
PR-G2-03 local objective
PR-G2-04 local environment UI

PR-G3-01 maximin selector
PR-G3-02 iterative optimizer
PR-G3-03 convergence + benchmark

PR-G4-01 GA genome/state
PR-G4-02 selection/mutation
PR-G4-03 GA UI
PR-G4-04 benchmark gate

PR-G5-01 PSO
PR-G5-02 external adapter
```

每个 PR 尽量只改变一个 architecture concern。

---

# 28. 建议 Codex 执行纪律

每个 phase 都要求 Codex：

1. 先阅读相关现有代码与测试。
2. 列出拟修改文件。
3. 先写/更新测试。
4. 实现最小闭环。
5. 运行相关测试。
6. 运行全量测试。
7. 报告实际改动与未完成项。
8. 不擅自扩大 phase scope。

特别要求：

```text
禁止为了方便复制现有 FPS / descriptor evaluator 逻辑。
禁止新建第二套 JobService。
禁止在前端硬编码一套与 backend 不同步的 generation catalog。
禁止把所有 generation 参数加入 AnalysisParams。
```

---

# 29. 推荐首个 Codex 开发任务

第一任务只做 G0，不做可见功能：

```text
Implement Phase G0 of the MDescriptorStudio Descriptor-Guided Dataset Expansion plan.

Goals:
1. Introduce generation package skeleton and strongly typed internal models.
2. Extract structural jitter and isotropic strain from job_runner into reusable structure operators without changing numerical behavior.
3. Introduce DescriptorEvaluator that preserves atomic values and row_offsets while supporting the existing structure-level mean-pooling path.
4. Extract the blocked exact nearest-distance primitive currently embedded in FPS into a shared implementation and make FPS consume it.
5. Refactor perturbation_sensitivity to use the new shared operator/evaluator path.
6. Add focused tests and preserve every existing test.

Do not implement generation RPC, database schema, UI, GA, PSO or new user-visible behavior in this phase.
```

只有 G0 全部通过后再进入 G1。

---

# 30. 推荐开发里程碑

## Milestone A — Foundation Ready

对应 G0。

成果：Generation 所需公共 primitive 已存在，现有行为零回归。

## Milestone B — First Generated Dataset

对应 G1。

成果：用户可以从已有 Dataset 运行 descriptor-guided random expansion 并 materialize 新 Dataset。

这是第一个可演示版本。

## Milestone C — Local Environment Discovery

对应 G2。

成果：系统能够明确回答：

> 哪些新结构提供了原训练集中没有覆盖的局域环境？

这是第一个具有明显科学特色的版本。

## Milestone D — Efficient Exploration

对应 G3。

成果：相同 descriptor evaluation budget 下，Maximin 比 Random 更有效地获取新环境。

## Milestone E — Evolutionary Search

对应 G4。

成果：GA 进入 experimental，且必须用 benchmark 证明其相对简单方法的收益。

## Milestone F — Extensible Search Platform

对应 G5。

成果：PSO 和 external generation engine 使用统一接口，MDescriptorStudio 成为 descriptor-driven structure/data exploration platform。

---

# 31. 最终验收场景

最终 Stable 版本至少完成以下完整流程：

```text
1. Register training dataset
2. Compute local descriptor
3. Open Generation
4. Select Local Environment Diversity
5. Set geometry constraints
6. Select Maximin or GA
7. Set 10,000 descriptor-evaluation budget
8. Run generation
9. Inspect convergence and descriptor-space expansion
10. Click a novel environment and locate atom in 3D structure
11. Materialize accepted structures as new Dataset
12. Compute descriptor on expanded Dataset
13. Compare coverage before vs after
```

该流程中所有中间结果必须：

- 可复现。
- 可取消。
- 可审计。
- 不修改源数据。
- 有完整 lineage。
- 有明确 descriptor feature-space identity。

---

# 32. 科学方法主线

整个模块最终应围绕一个明确问题组织：

> **给定一个已有原子结构数据集，在有限 descriptor evaluation 预算下，如何主动生成新的物理合理结构，使数据集包含尽可能丰富且非冗余的局域原子环境？**

因此 GA、PSO、SSW 不应成为产品核心叙事。

产品/论文主线应该是：

```text
Descriptor-guided dataset expansion
        ↓
Local-environment novelty
        ↓
Coverage-aware archive
        ↓
Budget-efficient structure exploration
```

GA / PSO / External SSW 是可替换的 search engine。

真正属于 MDescriptorStudio 的核心能力是：

```text
Structure generation
        +
Descriptor evaluation
        +
Local-environment archive
        +
Novelty / coverage objective
        +
Dataset lineage
```

---

# 33. 推荐当前立即执行顺序

当前不要直接开始写 GA。

严格执行：

```text
NOW
│
├─ G0.1 package skeleton
├─ G0.2 operator extraction
├─ G0.3 DescriptorEvaluator
├─ G0.4 shared exact distance
├─ G0.5 perturbation regression
│
↓
G1 Random Expansion MVP
│
↓
G2 Local Environment Expansion
│
↓
G3 Maximin Expansion
│
↓
Benchmark Gate
│
↓
G4 Genetic Algorithm
│
↓
Benchmark Gate
│
↓
G5 PSO / External SSW
```

这个顺序可以最大程度降低软件工程风险，也能够确保每增加一种复杂搜索算法之前，先证明目标函数、约束、descriptor evaluation 和 archive 本身是正确的。

---

## 34. Definition of Done

整个项目的最终 Definition of Done：

- [ ] Generation 是独立一级模块，而不是 Analysis Sampling 的算法选项。
- [ ] Random / Maximin / GA / PSO 共用同一 evaluator/objective/constraint/archive。
- [ ] 支持 structure-level 与 local-environment-level descriptor objective。
- [ ] 所有优化均基于完整 scaled descriptor space，而不是 t-SNE/UMAP 距离。
- [ ] Local Environment 模式保留 atom values 与 row_offsets。
- [ ] 物理/几何约束在 descriptor compute 前执行。
- [ ] 所有长任务可以取消。
- [ ] 所有 run 有明确固定 seed 与可复现 metadata。
- [ ] 大数组存 artifact，不滥用 SQLite。
- [ ] Materialize 产生新 Dataset，不修改父数据。
- [ ] dataset_lineage 完整记录父子关系。
- [ ] 固定 evaluation budget 下有 Random / Maximin / GA / PSO 公平 benchmark。
- [ ] GA/PSO 的存在由 benchmark 收益而不是算法名称驱动。
- [ ] 外部 SSW 通过通用 ExternalGeneratorAdapter 接入。
- [ ] 前后端 generation vocabulary 有单一权威来源或 parity gate。
- [ ] 全部已有 Analysis、Sampling、Perturbation、Dataset 流程无回归。

---

**建议状态：先实施 G0，不并行开发 G4/G5。**
