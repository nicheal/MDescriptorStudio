# MDescriptorStudio — Descriptor-Guided Dataset Expansion 开发计划

> 功能名称：**Descriptor-Guided Dataset Expansion**（描述符引导的数据集扩展）
> 定位：与 Sampling（已有结构中选哪些）并列的一级后端能力（产生哪些新结构）。二者共享 descriptor、距离、coverage、Jobs、artifact 与 Dataset lineage，但生命周期独立。
> 本轮范围：**Phase G0–G2**（已确认）。G3–G5 为后续开发路线，附于文末。
> UI 文案：英文 + 现有 i18n t() 机制。

---

## 0. 科学工作流与架构原则

```text
Seed Dataset → Seed Selection → Structure Generator
  → Geometry / Physics Constraints（先于 descriptor 计算）
  → MDescriptor Evaluation（batch）
  → Descriptor Objective（Novelty / Local environment diversity）
  → Archive / Population Update（每轮一次，frozen archive）
  → Iteration → Accepted Structures → Create New Dataset
```

核心原则：**Generator ≠ Objective ≠ Constraint ≠ Descriptor**。算子、约束、目标、评估器全部走 Protocol 接口 + registry 注册，未来 GA/PSO/External 只替换 optimizer 一个插槽。

性能纪律（仅有的两个热点）：
- 一次 compute 128 结构（batch evaluate），绝不逐结构 compute；
- 分块 nearest-distance（O(N·M·D) 内存有界，_BLOCK=2048）；
- frozen archive 每轮更新一次，结果可复现且不被 selection order 干扰。

---

## 1. Phase G0 — 重构基础（纯重构，现有测试全绿）

抽取三个公共件，删除被替代代码（AGENTS.md：不留双路径）：

| 新文件 | 来源 | 说明 |
|---|---|---|
| `generation/_distance.py` | `analysis/sampling/fps.py` 的 `_min_sqdist_to_set` / `_sqdist_to_point` | 公共接口 `min_distance_to_reference(query, reference, block_size=2048)`；保持显式差分（拒绝 ‖x‖²−2x·p+‖p‖² 展开式，精度原因，fps.py 注释已说明）；fps.py 改调公共实现 |
| `generation/operators/` | `services/job_runner.py:660 _perturb_frame` | displacement.py（jitter）、strain.py（各向同性/各向异性，cell+positions 同一仿射保持分数坐标）；job_runner 删除 `_perturb_frame`，perturbation_sensitivity 改调公共算子 |
| `generation/evaluator.py` | `services/job_runner.py:677 _computed_structure_values` | `DescriptorEvaluator`：`evaluate_structures(structures, *, return_atomic=False) -> DescriptorEvaluation(structure_values, atomic_values, row_offsets)`；保留池化方法供 perturbation sensitivity 调用；新功能走非池化路径，不做不可逆平均 |

验收：`tests/test_fps_sampling.py` 等现有测试全量通过。

---

## 2. Phase G1 — Descriptor-Guided Random Expansion

### 2.1 数据契约（`generation/models.py`，强类型，不传 dict）

```python
@dataclass
class DescriptorEvaluation:
    structure_values: np.ndarray          # (n_structures, n_features)
    atomic_values: np.ndarray | None      # (n_atoms_total, n_features)
    row_offsets: np.ndarray | None        # (n_structures + 1,)

@dataclass
class StructureCandidate:
    candidate_id: str
    atomic_numbers / positions / cell / pbc
    parent_frame: int | None
    parent_candidate_id: str | None
    generation: int
    operator: str
    operator_params: dict
    metadata: dict

@dataclass
class CandidateEvaluation:
    candidate_id: str
    valid: bool
    rejection_reason: str | None
    structure_descriptor / atomic_descriptors
    novelty: float | None
    local_diversity: float | None
    penalty: float
    fitness: float

@dataclass
class ArchiveEntry:
    candidate_id: str
    structure_index: int
    fitness: float
    novelty: float
    generation: int
```

### 2.2 接口（Protocol + registry）

```python
class StructureOperator(Protocol):
    name: str
    def apply(self, parent, rng, params) -> StructureCandidate: ...

class GeometryConstraint(Protocol):
    def validate(self, candidate) -> ConstraintResult(valid, penalty, reasons): ...

class GenerationObjective(Protocol):
    name: str
    def evaluate(self, candidate, archive) -> ObjectiveResult(score, components): ...
```

- **Objectives（G1）**：`objectives/novelty.py` — 结构级 `N(X)=min_Y‖z̃(X)−z̃(Y)‖₂`；scaling 复用 `analysis/sampling/preprocessing.py`（raw/standardized/robust，默认 robust），**scaler 只由 Reference Dataset 拟合一次**（不得每代重拟合，保证 fitness 空间可比较）。
- **Operators（G1）**：atomic displacement（0–0.15 Å）、isotropic strain（±5%）、anisotropic strain（±5%）、cell shear。species_swap/crossover/vacancy/interstitial 留 GA v2。
- **Constraints（G1）**：`constraints/geometry.py` — NaN/Inf、cell determinant、最小原子间距（Absolute 与 Covalent-radius based `r_ij > α(r_i^cov+r_j^cov)`，α 默认 0.7 可调，复用 `datasets/covalent_radii.py`）、过大位移、过大应变、体积变化（默认 20%）、组分/原子数锁定、重复结构。
- **Optimizer（G1）**：`optimizers/random_search.py` — 描述符引导随机扩展。
- **Archive（G1）**：`archive.py` `DescriptorArchive` — nearest/add/contains_near/coverage/size，内部走 `_distance.py`。MVP 无 FAISS。

### 2.3 Engine 迭代循环（`generation/engine.py`）

```python
for iteration in range(max_iterations):
    seeds = choose_seeds(seed_pool, n_seed)          # 64 batch / 8 elite / 8 children per seed
    candidates = [operator.apply(seed, rng) for seed in seeds for _ in range(k)]
    candidates = [c for c in candidates if constraints(c).valid]   # cheap filter 先于 descriptor
    evaluation = evaluator.evaluate_structures(candidates)          # 一次 batch compute
    scores = objective.evaluate_batch(evaluation, archive)          # 对 frozen archive 打分
    selected = greedy_max_min_select(candidates, scores, budget=8)  # batch 内去重：
    #   score(X) = α·d(X,A) + (1−α)·d(X,S)，S 为本批已选中候选
    archive.add(selected)                                           # 每轮一次更新
    if stop_criteria.reached(): break
```

StopCriteria：max_generations / max_evaluations / max_accepted / target_novelty / no_improvement_rounds。

### 2.4 持久化与任务

- **Migration 12**（`storage/database.py`）：

```sql
CREATE TABLE generation_runs (
    id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    descriptor_run_id TEXT,
    optimizer TEXT NOT NULL,
    objective TEXT NOT NULL,
    params_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
    evaluations INTEGER DEFAULT 0, accepted_count INTEGER DEFAULT 0,
    result_path TEXT,
    artifact_manifest_json TEXT, preview_json TEXT, warnings_json TEXT,
    cache_key TEXT, stale_reason TEXT, updated_at TEXT
);
CREATE INDEX idx_generation_dataset ON generation_runs(dataset_id);
CREATE INDEX idx_generation_status ON generation_runs(status, created_at);
ALTER TABLE jobs ADD COLUMN generation_run_id TEXT;
```

不存原子坐标/描述符到 SQLite，大数组全部写 artifact。

- **JobService**：`_category()` 增加 `"generation."` 分类与独立池；submit 支持 generation_run_id；`_settle_linked_runs` / `_sweep_zombie_runs` 覆盖 generation_runs；`protocol/server.py` CONTROL_METHODS 增加 `generation.get/list`（轮询不被长任务堵塞）。
- **服务**：`services/generation_service.py`（submit/get/list/preview/cancel/materialize/export，_submit_lock 防重复提交，_LIST_COLUMNS 模式照抄不取 preview_json）+ `services/generation_runner.py`（job 侧 mixin，ctx.progress/check_cancelled 接 engine 回调）。

### 2.5 Artifact（`generation/artifacts/writer.py` / `reader.py`）

目录 `generation/{gen_id}/`（id 匹配 `^gen_[A-Za-z0-9_-]{1,64}$`）：

```text
manifest.json, metadata.json, accepted.extxyz, candidates.jsonl,
fitness.npy, novelty.npy, coverage_gain.npy, generation.npy,
parent_index.npy, operator.npy, structure_descriptors.npy,
local_environment_descriptors.npy (G2), local_row_offsets.npy (G2),
convergence.json
```

- 原子发布：staging `.{id}.tmp-{uuid}` → manifest `completed: true` → `os.replace`（仿 `AnalysisArtifactStore` 模式）；中断不产生半成品。
- `accepted.extxyz` 帧级 Info 字段：`generation_id / candidate_id / parent_frame / generation / operator / fitness / novelty / local_novelty` — 脱离软件仍可追溯。
- `convergence.json`：每轮增量（evaluations、accepted、best/mean novelty、coverage radius、拒绝计数 Geometry/Duplicate/Low novelty、novel environment 数），驱动实时曲线。

### 2.6 Cache key 与 descriptor signature

cache key = dataset fingerprint + descriptor signature + optimizer + objective + operators + constraints + budget + seed + algorithm_version。descriptor signature 记录进 artifact metadata：descriptor_name / descriptor_version / engine_version / descriptor_parameters / feature_count / row_semantics / scaling / scaling_parameters。**seed=random 则不缓存**；UI 默认 seed=42。

### 2.7 RPC（`main.py` 注册）

```text
generation.catalog / submit / get / list / preview / cancel / materialize / export
```

submit 支持嵌套 object（objective / operators / constraints / budget / seed）。**契约测试同步**：test_mock_backend_vocabulary.py 与 test_wire_contract_parity.py 必须同步新增 generation.* 词汇，否则 CI 失败。

### 2.8 Materialize

accepted.extxyz 写出 → 复用 `dataset_service.py` 现有注册管线（datasets + dataset_statistics + dataset_lineage），lineage **operation = "dataset_generation"**（具体 optimizer 放 metadata，避免 vocabulary 失控）。

---

## 3. Phase G2 — Local Environment Expansion（科学价值核心）

- `DescriptorEvaluator.evaluate_structures(..., return_atomic=True)`：保留 atomic_values + row_offsets，不做不可逆平均。
- **两层 Archive**：`DescriptorArchive`（结构级）+ `LocalEnvironmentArchive`（原子环境级）。
- **local_diversity 目标**（`objectives/local_diversity.py`）：原子级 `d_i = min_j ‖z_i − a_j‖`，聚合：
  - Mean；
  - **Top-Q Mean（默认 Top 20% mean，优化用）**；
  - Quantile；
  - Maximum（仅诊断展示，防单异常原子主导）。
- **Novel 环境计数**：`N_new = Σ I(d_i > d_novel)` 与占比 `N_new/N`；UI 展示 novel fraction（默认阈值 0.25）。
- **composite 目标**（`objectives/composite.py`）：`F = w_l·F_local + w_s·N_structure`，默认 preset **Local 0.7 / Structure 0.3**（提供 preset，不设为唯一科学默认）。
- Artifact 扩展：local_environment_descriptors.npy（仅 accepted 候选，可选 float32 控体积）+ local_row_offsets.npy。
- Scaler 同样只由 reference 拟合一次，应用于原子级矩阵。

---

## 4. 前端（独立一级 Generation 页面）

- `App.tsx` TABS 增加 "generation"（lazy 加载）；`stores/workspace.ts` page 联合类型扩展。
- **独立 `features/generation/`**：`useGenerationStore`（不扩大 AnalysisParams）、`types.ts` 用 discriminated union：

```typescript
export type OptimizerConfig =
  | { type: "random"; batchSize: number }
  | { type: "genetic"; populationSize: number; eliteSize: number; mutationRate: number; tournamentSize: number }
  | { type: "pso"; particles: number; inertia: number; cognitive: number; social: number };

export type GenerationObjective =
  | "novelty" | "local_environment_novelty" | "coverage" | "target_region" | "composite";
```

- **页面单页三态**（仿 Analysis.tsx 拆分：Generation.tsx + generationConfig.tsx + generationRun.tsx + generationResults.tsx）：
  - **配置态**：标题 "DATASET EXPANSION / Explore structure space guided by descriptor diversity"；六段区块（antd Card，Fluent 风格 #FFFFFF/#0F6CBD/#616161/#EAECF0）：
    1. SOURCE：Dataset / Descriptor run / Representation / Seed scope
    2. SEARCH OBJECTIVE：Local environment diversity / Structure novelty 勾选；Aggregation（Top 20% mean）、Feature scaling（Robust）、Novel threshold（0.25）
    3. SEARCH SPACE：四算子开关 + 幅度（Atomic displacement 0–0.15 Å；Isotropic/Anisotropic strain ±5%；Cell shear）
    4. PHYSICAL CONSTRAINTS：Min distance（Covalent × 0.70 可调）、Max volume change 20%、Composition/Atom count Locked
    5. OPTIMIZER：Method（Descriptor-guided random）、Candidate batch 64、Accepted/round 8
    6. COMPUTE BUDGET：Max evaluations 10 000、Max accepted 500、Target novelty 0.25、Seed（Fixed 42 / Random）+ Run Expansion 按钮
  - **运行态**：指标卡（Generation/Evaluations/Accepted/Best novelty/Mean novelty/Coverage radius）+ 拒绝计数（Geometry/Duplicate/Low novelty）+ 两条实时折线（Novel environments vs Evaluations、Coverage radius vs Evaluations）+ Cancel。
  - **结果态**：PCA 三色散点（gray=original、blue=evaluated candidates、orange=accepted）+ generation slider；**LOCAL ENVIRONMENT DISCOVERY** 面板（Original / Generated / Novel environments 计数与 Novel fraction；点击新区域 → structure → atom → 3Dmol highlight，可作后续增强项）；accepted 列表 + Materialize / Export。
- 图表沿用现有 Plotly 体系；i18n 走 t()。

---

## 5. 测试矩阵

| 层 | 核心验证 | 文件 |
|---|---|---|
| Operator | 扰动数学正确（分数坐标保持） | tests/test_generation_models.py |
| Constraint | 正确拒绝碰撞结构（共价/绝对两种模式） | 同上 |
| DescriptorEvaluator | row_offsets 正确、池化与非池化路径 | 同上 |
| Archive | nearest 与 brute force 一致（两层） | 同上 |
| Objective | §40 解析解：existing {(0,0),(0,1),(1,0),(1,1)}，A=(0.5,0.5) B=(3,3) ⇒ Novelty(B)>Novelty(A)；B1=(3,3) B2=(3.01,3.01) C=(−2,−2) 选两个 ⇒ B1+C 而非 B1+B2 | tests/test_generation_objectives.py |
| Local objective | §41：A={0,1,2,3}；X1={0.1,1.1,2.1,3.1} X2={0.1,1.1,8.0,9.0} ⇒ Top-20% 显著判 X2；X3 含 1000 ⇒ Max 被单点支配、Top-Q 稳定 | 同上 |
| Random expansion | 固定 seed 完全可重复；frozen archive；停止条件 | tests/test_generation_engine.py |
| IPC | submit/cancel/materialize 契约 | tests/test_generation_ipc.py |
| Artifact | 中断不产生半成品；extxyz 元数据可追溯 | tests/test_generation_artifacts.py |
| Migration | Migration 12 用例 | tests/test_database_migrations.py（改） |
| 词汇契约 | mock backend / wire parity 同步 | 两个契约测试（改） |
| UI | 配置准确序列化 | frontend vitest |
| E2E | Dataset → Generate → Materialize 冒烟 | frontend/e2e/generation.spec.ts |

---

## 6. 实施顺序与验收

1. **G0**：抽 `_distance.py` → 跑 test_fps_sampling.py → 抽 operators/evaluator → 删旧代码 → 全量回归。
2. **G1 后端核心**：models/registry/engine/archive/random_search/novelty/geometry + 单测。
3. **G1 持久化**：Migration 12 → JobService 集成 → artifact 原子发布 → cache key → RPC 8 方法 + 契约测试同步。
4. **G1 Materialize + UI**：extxyz → 注册 + lineage → Generation 页面三态 + e2e 冒烟。此时可发布 experimental 功能。
5. **G2 局域环境**：return_atomic 路径 → LocalEnvironmentArchive → local_diversity/composite + 解析单测 → artifact 扩展。
6. **G2 UI**：PCA 三色 + slider → Local Environment Discovery 面板 → 实时收敛曲线 → local objective 配置项。

---

## 7. 后续开发路线（本轮之后，不在本次范围内）

### Phase G3 — Iterative Maximin Expansion
- candidate batch 中完整接入 FPS（复用 `farthest_point_sampling`，warm-start 到 archive）；
- selection 升级为 archive novelty + population diversity 的 greedy max-min 完整实现（替代 G1 的简化 α 加权）；
- coverage / novelty 收敛曲线与 coverage objective（明确区分 Novelty Expansion vs Coverage Completion——后者需指定 reference pool）；
- 停止条件升级为新环境发现率饱和判据 `ΔN_new/ΔN_eval < ε`（连续 10 代每 100 evaluations 新增环境 < 1）。

### Phase G4 — Genetic Algorithm
- perturbation genome（parent_frame / displacement_sigma / strain 分量 / shear 分量 / seed），GA 优化"如何扰动已有结构"而非直接优化坐标（不同结构原子数/排列/晶胞不同，直接坐标交叉难定义）；
- 默认参数：Population 64 / Elites 8 / Tournament 3 / Mutation 0.20 / Generations 50；
- v1 不做 crossover（selection + mutation 的 evolutionary strategy），先验证核心命题 **GA > Random（相同 evaluations 下）**；
- benchmark 指标：固定 10 000 descriptor evaluations，比较 Accepted 数 / Coverage Radius ↓ / Novel Env ↑（CPC 论文方法验证表）；
- 仅当 GA 有优势才做 crossover 与 NSGA-II（Pareto front：max local novelty + max structural diversity + min energy）。

### Phase G5 — PSO / External Search
- PSO：`x_i = [Δr_1..Δr_N, ε_xx, ε_yy, ...]` 标准更新；objective 多峰不光滑，保持 10–20% random immigrants 防早熟；
- `ExternalGenerationAdapter`（prepare/run/collect 接口，SSW/LASP 只是 backend 之一，未来可接 AIRSS/USPEX/CALYPSO/LAMMPS/ASE optimizer）；
- Target Region Search（PCA 框选区域目标 `F = −‖P(z(X)) − t‖`，PCA 线性可参与 objective；UMAP/t-SNE 仅可视化）；
- 能力扩展：energy/force/uncertainty 约束（MLP 筛选，"Geometrically valid" vs "Energetically screened" 两层）。

### 术语与科研主线
```text
Representative Sampling          已有数据 → 减少数据
Descriptor-Guided Expansion      已有数据 → 生成新数据
```
闭环：Dataset → Descriptor → Analysis → Identify sparse regions → Generate new environments → Expanded Dataset → Descriptor → Analysis。
GA、PSO、SSW 只是闭环里的搜索引擎；MDescriptorStudio 的核心方法是"以描述符空间覆盖和局域环境新颖性驱动结构生成"——这是 CPC 论文的方法学主线。
