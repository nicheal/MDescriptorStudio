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
    atomic_numbers: np.ndarray
    positions: np.ndarray
    cell: np.ndarray
    pbc: np.ndarray
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
    structure_descriptor: np.ndarray | None
    atomic_descriptors: np.ndarray | None
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
    def apply(self, parent: StructureCandidate, rng: np.random.Generator, params: dict) -> StructureCandidate: ...

class GeometryConstraint(Protocol):
    def validate(self, candidate: StructureCandidate) -> ConstraintResult: ...  # (valid, penalty, reasons)

class GenerationObjective(Protocol):
    name: str
    def evaluate(self, candidate: CandidateEvaluation, archive: DescriptorArchive) -> ObjectiveResult: ...  # (score, components)
```

- **Objectives（G1）**：`objectives/novelty.py` — 结构级 `N(X)=min_Y‖z̃(X)−z̃(Y)‖₂`；scaling 复用 `analysis/sampling/preprocessing.py`（raw/standardized/robust，默认 robust），**scaler 只由 Reference Dataset 拟合一次**（不得每代重拟合，保证 fitness 空间可比较）。
- **Operators（G1）**：atomic displacement（0–0.15 Å）、isotropic strain（±5%）、anisotropic strain（±5%）、cell shear。species_swap/crossover/vacancy/interstitial 留 GA v2。
- **Constraints（G1）**：`constraints/geometry.py` — NaN/Inf、cell determinant、最小原子间距（Absolute 与 Covalent-radius based `r_ij > α(r_i^cov+r_j^cov)`，α 默认 0.7 可调，复用 `datasets/covalent_radii.py`）、过大位移、过大应变、体积变化（默认 20%）、组分/原子数锁定、重复结构。
- **Optimizer（G1）**：`optimizers/random_search.py` — 描述符引导随机扩展（首个算法）。
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

- **Migration 12**（`storage/database.py`，现到 v11）：

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

- **JobService**：`_category()` 增加 `"generation."` 分类与独立池；submit 支持 generation_run_id；`_settle_linked_runs` / `_sweep_zombie_runs` 覆盖 generation_runs；`protocol/server.py` CONTROL_METHODS（现 {"job.cancel","job.get","job.list"}）增加 `generation.get/list`（轮询不被长任务堵塞）。
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
- 路径安全沿用 `security.py` validate_managed_path / ensure_no_reparse_points / remove_managed_tree / open_text_for_write。

### 2.6 Cache key 与 descriptor signature

cache key = dataset fingerprint + descriptor signature + optimizer + objective + operators + constraints + budget + seed + algorithm_version。descriptor signature 记录进 artifact metadata：descriptor_name / descriptor_version / engine_version / descriptor_parameters / feature_count / row_semantics / scaling / scaling_parameters。**seed=random 则不缓存**；UI 默认 seed=42。

### 2.7 RPC（`main.py` 注册）

```text
generation.catalog / submit / get / list / preview / cancel / materialize / export
```

submit 支持嵌套 object（objective / operators / constraints / budget / seed），示例：

```json
{
  "dataset_id": "ds_xxx",
  "descriptor_run_id": "run_xxx",
  "optimizer": "random",
  "objective": {"type": "local_environment_novelty", "aggregation": "top_fraction_mean", "top_fraction": 0.20},
  "operators": {"atomic_displacement": {"enabled": true, "max_sigma": 0.15}, "strain": {"enabled": true, "max_strain": 0.08}},
  "constraints": {"min_distance_mode": "covalent", "min_distance_factor": 0.7},
  "budget": {"max_evaluations": 10000, "max_accepted": 500},
  "seed": 42
}
```

**契约测试同步**：test_mock_backend_vocabulary.py 与 test_wire_contract_parity.py 必须同步新增 generation.* 词汇，否则 CI 失败。

### 2.8 Materialize

accepted.extxyz 写出 → 复用 `dataset_service.py` 现有注册管线（datasets + dataset_statistics + dataset_lineage，:334-370 事务模式），lineage **operation = "dataset_generation"**（具体 optimizer 放 metadata，避免 vocabulary 失控）。

---

## 3. Phase G2 — Local Environment Expansion（科学价值核心）

- `DescriptorEvaluator.evaluate_structures(..., return_atomic=True)`：保留 atomic_values + row_offsets，不做不可逆平均。结构级 novelty 用 structure_values；局域环境 novelty 直接用 atomic_values。
- **两层 Archive**：`DescriptorArchive`（结构级 `[structure, feature]`）+ `LocalEnvironmentArchive`（原子环境级 `[atom environment, feature]`）。
- **local_diversity 目标**（`objectives/local_diversity.py`）：原子级 `d_i = min_j ‖z_i − a_j‖`，聚合：
  - Mean；
  - **Top-Q Mean（默认 Top 20% mean，优化用）**；
  - Quantile；
  - Maximum（仅诊断展示，防单异常原子主导）。
- **Novel 环境计数**：`N_new = Σ I(d_i > d_novel)` 与占比 `N_new/N`；UI 展示 novel fraction（默认阈值 0.25）。可组合 `F = w_1·F_local + w_2·(N_new/N)`，引导算法寻找"包含大量新局域环境的结构"而非单个异常原子。
- **composite 目标**（`objectives/composite.py`）：`F = w_l·F_local + w_s·N_structure`，默认 preset **Local 0.7 / Structure 0.3**（提供 preset，不设为唯一科学默认）。
- Artifact 扩展：local_environment_descriptors.npy（仅 accepted 候选，可选 float32 控体积）+ local_row_offsets.npy。
- Scaler 同样只由 reference 拟合一次，应用于原子级矩阵。

---

## 4. 前端（独立一级 Generation 页面）

- `App.tsx` TABS 增加 "generation"（lazy 加载，现有 overview/explore/descriptors/results/analysis 之后）；`stores/workspace.ts` page 联合类型扩展。
- **独立 `features/generation/`**：`useGenerationStore`（不扩大 AnalysisParams）、`types.ts` 用 discriminated union（杜绝大量 optional 字段的非法状态）：

```typescript
export type GenerationObjective =
  | "novelty" | "local_environment_novelty" | "coverage" | "target_region" | "composite";

export type OptimizerConfig =
  | { type: "random"; batchSize: number }
  | { type: "genetic"; populationSize: number; eliteSize: number; mutationRate: number; tournamentSize: number }
  | { type: "pso"; particles: number; inertia: number; cognitive: number; social: number };
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
  - **结果态**：PCA 三色散点（gray=original、blue=evaluated candidates、orange=accepted）+ generation slider（拖动 Generation 0→50 看采样区域外扩）；**LOCAL ENVIRONMENT DISCOVERY** 面板（Original / Generated / Novel environments 计数与 Novel fraction，如 213 482 / 51 320 / 7 842 / 15.3%；点击新区域 → structure → atom → 3Dmol highlight，可作后续增强项）；accepted 列表 + Materialize / Export。
- 图表沿用现有 Plotly 体系；i18n 走 t()；左侧导航最终形态：Dataset / Explore / Descriptor / Analysis / Generation / Results。

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
- coverage / novelty 收敛曲线与 coverage objective（明确区分 Novelty Expansion vs Coverage Completion——后者需指定 reference pool，例如从 MD trajectory 百万结构中标注一万，FPS 非常合适）；
- 停止条件升级为新环境发现率饱和判据 `ΔN_new/ΔN_eval < ε`（连续 10 代每 100 evaluations 新增环境 < 1，即新环境发现率已饱和）。

### Phase G4 — Genetic Algorithm
- perturbation genome（parent_frame / displacement_sigma / strain 分量 / shear 分量 / seed），GA 优化"如何扰动已有物理结构"而非直接优化坐标（不同结构原子数/排列/晶胞不同，直接坐标交叉难定义）；
- 默认参数：Population 64 / Elites 8 / Tournament 3 / Mutation 0.20 / Generations 50；
- v1 不做 crossover（selection + mutation 的 evolutionary strategy），先验证核心命题 **GA > Random（相同 evaluations 下）**；
- benchmark 指标：固定 10 000 descriptor evaluations，比较 Accepted 数 / Coverage Radius ↓ / Novel Env ↑（CPC 论文方法验证表）；
- 仅当 GA 有优势才做 crossover 与 NSGA-II（Pareto front：max local novelty + max structural diversity + min energy；用户自选 Extreme diversity / Balanced / Low-energy diversity）。

### Phase G5 — PSO / External Search
- PSO：`x_i = [Δr_1..Δr_N, ε_xx, ε_yy, ...]` 标准更新（ω、c_1、c_2）；descriptor novelty objective 多峰不光滑，保持 10–20% random immigrants 防早熟收敛；
- `ExternalGenerationAdapter`（prepare/run/collect 接口，SSW/LASP 只是 backend 之一，未来可接 AIRSS/USPEX/CALYPSO/LAMMPS/ASE optimizer）；
- Target Region Search（PCA 框选区域目标 `F = −‖P(z(X)) − t‖`，PCA 线性确定性可参与 objective；UMAP/t-SNE 仅可视化，不作优化空间）；
- 能力扩展：energy/force/uncertainty 约束（MLP 筛选，架构上分 "Geometrically valid" 与 "Energetically screened" 两层；MVP 不强制能量以免立刻依赖 DFT/MLP）；
- Composite 归一化：不同量纲先 robust 归一化或 population rank，再组合。

### 术语与科研主线
```text
Representative Sampling          已有数据 → 减少数据
Descriptor-Guided Expansion      已有数据 → 生成新数据
```
第一种方法：**Iterative Maximin Expansion**（迭代最大最小距离扩展）。
闭环：Dataset → Descriptor → Analysis → Identify sparse regions → Generate new environments → Expanded Dataset → Descriptor → Analysis。
GA、PSO、SSW 最终都只是闭环里的搜索引擎；MDescriptorStudio 的核心方法是"以描述符空间覆盖和局域环境新颖性驱动结构生成"——这是 CPC 论文最应强调的方法学主线。

---

## 8. 实施状态（2026-09-24，G0–G2 已交付）

| 阶段 | 状态 | 验证 |
| --- | --- | --- |
| G0 重构基础 | ✅ | `generation/_distance.py`、`operators/`（displacement/strain/shear + 候选级类）、`evaluator.py`（池化 + 非池化双路径）；`job_runner.py` 删除 `_perturb_frame`/`_computed_structure_values`；112 项直接相关测试 + 全量 456 项回归通过 |
| G1 后端核心 | ✅ | models/registry/engine/archive（两层 + greedy max-min 批内去重）/random_search/novelty/local_diversity/composite/geometry 约束；40 项单测（含 §40/§41 解析解、固定 seed 可重复、frozen archive） |
| G1 持久化/Jobs/RPC | ✅ | Migration 12、generation 作业池、settle/zombie 覆盖、CONTROL_METHODS、artifact 原子发布（中断无半成品）、cache key（random seed 不缓存）、RPC 9 方法（含 generation.pca）、契约 golden 已更新 |
| G1 Materialize + UI | ✅ | lineage `dataset_generation`；Generation 页面三态 + useGenerationStore + App tab + i18n（中英）；e2e 冒烟通过 |
| G2 局域环境 | ✅ | 两层 Archive、Top-Q mean（默认 20%）/mean/quantile/max、N_new 计数、composite 0.7/0.3、artifact local_* 与 evaluated_* 数组、generation.pca；真实引擎（ACE，GaAs 12×64）端到端 IPC 测试通过 |
| G2 UI | ✅ | PCA 三色散点 + generation slider、LOCAL ENVIRONMENT DISCOVERY 真实计数、两条收敛曲线、local objective 配置项 |

测试基线：后端 456 通过（5 项失败为改动前已存在的引擎版本漂移问题，经 git stash 基线验证）；前端 tsc/eslint 干净，e2e 通过。遗留：点击 PCA 新区域 → 3Dmol 高亮（计划中为可选增强）；G3–G5 按第 7 节路线推进。

## 9. G3 实施状态（2026-09-24，已交付）

| 项 | 实现 | 验证 |
| --- | --- | --- |
| FPS 框架级批内选择 | `engine.select_diverse_batch()`：fitness 精英池（top 4×budget）→ `farthest_point_sampling`（替换 G1 简化 greedy max-min；删除 `greedy_max_min_select`/`selection_weight`，单一实现路径） | §40 解析解（B1+C 而非 B1+B2）；44 项单测 |
| Coverage Completion 目标 | `objectives/coverage.py`：`gain = R_cov(archive) − R_cov(archive∪X)`，R_cov 为 reference pool 覆盖半径；novelty 保留为诊断分量；注册进 registry/catalog，UI 目录自动出现 | 解析解：单位方块角点场景 gain((0.5,0.5))=√2−√0.5、远点 gain=0、空 archive 有限 fitness |
| 发现率饱和停止 | `Budget.discovery_window`（默认 10 轮）+ `min_novel_per_100_evals`（默认 1.0）；尾窗新环境数 < 阈值即 `stopped_by="discovery_saturated"` | 零位移场景 2 轮触发；零阈值不误停 |
| 前后端同步 | catalog/mock/store/submission 移除 selection_weight；mock 目录加 coverage | 后端 377 全绿（含真实引擎 IPC 生命周期）；前端 tsc/eslint 干净；e2e 通过 |

下一步：G4（GA，perturbation genome + GA>Random benchmark）。
