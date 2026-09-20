# 五条科学口径（2026-09-20）：现状 / 建议 / 失效影响

深度审阅第 4 步（`2026-09-19-deep-review.md:187`）里需要定调的部分。这五项不同于前面九批：它们改的是**算出来的数**，不是管道。所以每项都给出代码证据、我建议的口径、以及对已缓存结果的影响，请逐条批。

行号均为 2026-09-20 工作区实际状态（`main`，`a67f752`/`69183aa` 之后）。

## 0. 一览

| # | 口径 | 我的推荐 | 改变数值 | 需 bump | 工作量 |
|---|---|---|---|---|---|
| 1 | cross-dataset 三兄弟的默认尺度 | coverage 统一到 `standardized`，生效值回写并进缓存身份 | 是（coverage） | 是 | 后端 2 处 + 前端文案/契约 |
| 2 | 零方差判据 | 六处共用一个相对量级判据，`1e-12 × max(\|mean\|,1)` | 是（有极端列时） | 是 | 后端 1 helper + 6 调用点 |
| 3 | 配位数被显示预算削顶 | 配位数取真实接触数，截断只作用于邻居行 | 是（密排/大 cutoff） | 是 | 后端 2 行 + 前端 1 处读数 |
| 4 | acquisition 上报的 `scores` | 新增 `selection_scores` + `pool_mask`，旧 `scores` 只改名说明 | 否（先加不改） | 否 | 后端 + 前端数组契约 |
| 5 | strain 的缩放中心 | positions 与 cell 一律绕原点（仿射应变） | 是（strain 灵敏度） | 是 | 后端 2 行 + 3 条测试 |

**版本策略：批准的项一次 bump 完成**（见文末），不要逐项 bump——每 bump 一次就是所有历史分析结果一次全量重算。

---

## 口径 1：coverage 用原始尺度，overlap/acquisition 用标准化尺度（P1-14）

**现状。** 同一个 Coverage 面板上的两个 toggle 走两套尺度：

- `analysis/algorithms/pairs.py:14` — coverage：`_preprocess_reference_query(reference.values, query.values, params, "raw")`
- `pairs.py:33` overlap、`pairs.py:78` acquisition：`"standardized"`

前端不发送 `preprocess`：`features/analysis/submission.ts:103`（coverage/overlap 共用）与 `:67-81`（acquisition）的 params 里都没有这个键。后端也不回写生效值：coverage 的 preview 只有 `metric/q95/q99/...`（`pairs.py:29`），overlap（`:51-62`）与 acquisition（`:167-178`）同样缺 `preprocess`。于是 `job_runner.py:118` 的 `_canonical_params` 存成 `{"preprocess": null}`，**一条已缓存的 cross-dataset 结果无法回溯它是在哪个尺度上算的**。

**后果（审阅时实测）。** 把 query 的有信息列整体 ×1000：coverage 报 100% 覆盖，overlap 对同一对输入报 100% 全新——同一个面板、同一份输入、相反结论，而且用户唯一的操作是切了一个标签。真实风险不是"哪个更对"，而是描述符列天然混尺度（能量、力、体元归一化量），raw 下距离由方差最大的那一列主导，而这个主导关系随描述符版本静默变化。

**建议。** 三步，按顺序都有独立价值：

1. coverage 的默认改为 `"standardized"`，与 overlap/acquisition 一致（也与 sklearn 的距离型方法惯例一致：`NearestNeighbors` 之前普遍 `StandardScaler`）。
2. 在 `_preprocess_reference_query`（`_common.py:336-367`）把生效尺度返回给调用方，写进 preview 的 `preprocess` 字段。同层已有正确先例：`analysis/metrics/__init__.py:440-444` 的 `effective_dimension` 就是这么做的，而它的结果在 UI 里能显示"Standardized / Centered / Raw scale"（`pages/analysisEffectiveDimension.tsx:50-52`）——三条 cross 算法只是没跟上。
3. 让 `_canonical_params` 对 `CROSS_DATASET_TYPES`（`analysis_helpers.py:48`）填入生效尺度，使缓存身份区分"显式 raw"与"默认 standardized"。不做这步，将来任何默认调整都会让旧行继续按老 key 命中、却在新语义下被读。

可选（产品决策，不改代码语义）：在 Coverage 面板加尺度下拉，像 Sampling 面板的 `scaling` 那样把选择交给用户。**我不建议先做**——先把默认和记录定对，控件的价值才会显现。

**影响面。** 后端：`pairs.py:14`、`_common.py:336-367`、`job_runner.py:118`。前端：`analysisMethodGuides.ts` 的 coverage/overlap 方法学说明、契约金标 `tests/data/backend-response-keys.json`（preview 新字段）、mock `preview.tsx` 的对应 kind。

**失效。** 改变 coverage 的数值 → 需要 bump。

---

## 口径 2：零方差判据是绝对 eps，把纯舍入噪声当满权重信号轴（P1-15）

**现状。** 同一件事在仓库里有两种写法、互不等价：

- 绝对 eps：`_common.py:132`（`keep = scale > np.finfo(np.float64).eps`，随后 `:148` 除以该尺度）、`_common.py:357`、`:366`、`sensitivity.py:145`、`:163`、`correlation.py:112`、`_common.py:285`、`sampling/preprocessing.py:60-62`、`metrics/__init__.py:170`
- 命名阈值 `1e-12`：`correlation.py:20`（`variance_threshold`，用户可提交、会存进产物）、`metrics/__init__.py:91,215`（`constant_tolerance`，按 `np.ptp` 判，并回写 preview `:306`）

`float64` 的 eps 是 2.2e-16，比大多数真实描述符列在自身量级上的一个 ulp 还小。

**后果（审阅时实测）。** 某列在 1000.0 上只有几个 ulp 抖动（std≈2.4e-13）→ eps 判据保留它 → `standardized` 除以自身尺度后 std=1.0 → 每个距离、PCA、UMAP、coverage 阈值都掺进一条与真实列等权的噪声维；真实维 30 + 噪声维 20 时约 40% 的平方距离是噪声。同一列在 `correlation.py`（方差 > 1e-12）、`metrics/__init__.py`（ptp ≤ 1e-12）、`sampling/preprocessing.py`（eps）得到三种判决 → 面板之间对"这个 run 有哪些特征"直接互相矛盾。

**建议。** 一个 helper，六处调用，语义为**相对**量级：

```python
# _common.py：唯一的"这条列还有没有尺度"判据
SCALE_RELATIVE_TOLERANCE = 1e-12
def _meaningful_scale(mean, scale):
    return scale > SCALE_RELATIVE_TOLERANCE * np.maximum(np.abs(mean), 1.0)
```

`1e-12` 不是拍脑袋：它是仓库里已经作为**用户可见参数**存在的两个阈值（`variance_threshold`、`constant_tolerance`），所以这次改动是把"隐式判据"对齐到"已经暴露的那个数"，而不是引入第三个数。warning 文案不变（`_common.py:134`、`:360`）。

**必须说清的边界。** 相对 eps 判据只滤掉 **float64 舍入级**抖动。若某列是在 float32 精度上量化的（1000.0 量级、std≈1e-5），`1e-12 × 1e3 = 1e-9` 仍然保留它——那既可能是真信号也可能是源头精度，用一条绝对/相对阈值无法区分。要覆盖这种情况，只能是显式的 opt-in 参数（例如按各列 std 相对全体列中位 std 的比例丢列），那是另一项决策，我建议**本次不做**，先把六处判决统一、把最恶劣的放大路径堵掉。

**影响面。** 后端：`_common.py`（新增 helper，改 `:132/:357/:366/:285`）、`sensitivity.py:145,163`、`correlation.py:112`、`sampling/preprocessing.py:60-62`、`metrics/__init__.py:170`。产物：`feature_indices`（被丢列的掩码）变化会影响所有依赖它的面板 → 属预期。

**失效。** 改变含极端列的 run 的数值 → 需要 bump。

---

## 口径 3：配位数被显示预算静默削顶（P1-16）

**现状。** `analysis/algorithms/_common.py:585-587`：

```python
ordered = sorted(contacts, key=...)[:max_neighbors]
global_index = int(members[local_index])
coordination[global_index] = len(ordered)
```

先按 `max_neighbors` 截断，再用截断后的长度当配位数。`max_neighbors` 默认 128（`metrics/__init__.py:338`），前端硬编码 128（`submission.ts:122`）——它的定位是邻居 CSR 行/绘图的预算，不是物理量。

**后果（审阅时实测）。** 3000 原子、cutoff 3 Å、`max_neighbors=64` → `mean_coordination == max_coordination == 64`、`neighbor_count` 恰等于 `3000*64`。密排金属在 6 Å 下配位可达 80–120，默认 128 也会封顶。UI 直方图（`analysisVisualizations.tsx:549-556`）会画出一根钉在 128 的柱子，读者当物理解读用。

**建议。** `coordination[global_index] = len(contacts)`，`[:max_neighbors]` 只作用于 `row_indices`/`row_distances`（即邻居表和它的距离）。当 `len(contacts) > max_neighbors` 时追加一条 warning 并在 preview 记 `coordination_truncated_atoms`，让"邻居列表只保留了前 N 个"这件事可见。这条改动的方向由既有注释担保：`:581-583` 已经写明"两个不同周期镜像接触是不同近邻"，即配位数在语义上就是**接触计数**，不是列表长度。

**注意连带。** `pages/Explore.tsx:783` 的 "Local coordination" 读的是 `selectedLocalNeighbors.length`（邻居表长度），不是 coordination 数组。后端改完必须同批改这里，否则原子详情给截断值、直方图给真值，两个数同时出现在屏上。

**影响面。** 后端：`_common.py:585-588`、`metrics/__init__.py` preview（新增 `coordination_truncated_atoms`）。前端：`Explore.tsx:783` 改读数组、i18n 一条文案。测试：`tests/test_analysis_engine.py` 钉"配位数不受 `max_neighbors` 影响、邻居行受"。mock `preview.tsx:434/600` 的合成配位数。

**失效。** 改变高密度/大 cutoff 结果的 `coordination` → 需要 bump。

---

## 口径 4：acquisition 上报的 `scores` 不是驱动选择的那个目标（P1-17）

**现状。** 贪心循环里决策用的是**逐步更新**的目标（`pairs.py:136-141`：`min_diversity = np.minimum(...)` → `normalized_diversity` → `acquisition_score` → `argmax`），而循环结束后 `pairs.py:145-154` 用**最终态** `min_diversity` 另算一份 `full_scores` 上报，池外点一律 `0.0`。

**后果（审阅时实测）。** 选 12 个时上报分数序列 `[0.65, 0.618, 0.551, 0.462, 0.598, ...]`——第 12 名高于第 6–10 名。用户拿 `scores` 复核"为什么选它"，会得到与 `selected_indices` 相反的结论。而 `scores` 是经 `analysis_helpers.py:51-54` 的 `_PREVIEW_ARRAY_KEYS` 映射到 `point.score`（`analysisPreview.ts:20`、`Analysis.tsx:1419`）直接进表的。

**建议：先加、不改。** 

1. 新增数组 `selection_scores`（长度 = 实际选中数）：记录每个点**被选中那一刻**的目标值，天然单调不增，是"为什么选它"的正确解释量。
2. 新增数组 `pool_mask`（0/1）：池外点用它标注。**不能用 NaN 当哨兵**——出站帧走 `frames.encode(allow_nan=False, default=_unencodable)`，非有限值会让整帧变成错误帧（见项目记忆 IPC 边界），而且 `analysis.chunk` 会把数组直接 JSON 序列化。
3. `scores` 保持现有算法，但在 preview 里记 `score_semantics: "final_state"`，方法学说明（`analysisMethodGuides.ts` 的 acquisition 条目）改成"最终态排序；选择顺序请看 selection_scores"。

这样做的理由：口径 1/2/3/5 都要 bump，而这一条按"只增数组 + 改说明"实现就**不改变任何已存在的数**，因此它本身不需要 bump——省下一次全量重算里的一条理由，也让回滚只需删数组。代价是前端必须降级读取：`selection_scores` 缺失时回退到 `scores`，否则历史 acquisition 结果在新 UI 下画空图（同类先例：`analysisPreview.ts` 的 `hasColorByData()` / `narrowedArrays()`）。

**影响面。** 后端：`pairs.py:144-164`。契约：`ARTIFACT_ARRAYS`（`registry.ts:135` 同族的 per-kind 数组表）、`_PREVIEW_ARRAY_KEYS`、`analysisPreview.ts` point 字段、金标 keys、mock。测试：`test_analysis_engine.py` 钉"selection_scores 单调不增，且其 argmax 顺序 == selected_indices"。

**失效。** 按推荐实现 → 不需要 bump（若你更倾向直接把 `scores` 改成语义正确的逐步值，则需要，且历史行的表列含义会静默漂移）。

---

## 口径 5：strain 把 cell 绕原点缩放、把 positions 绕质心缩放

**现状。** `services/job_runner.py:453-462`：

```python
center = positions.mean(axis=0, keepdims=True)
scale = 1.0 + amplitude
cell = cell * scale                                  # 绕原点
return replace(frame, positions=center + (positions - center) * scale, cell=cell)   # 绕质心
```

两种写法的原子间距离都按 `scale` 缩放（这才是应变想测的东西），区别只在团簇落在盒子里的**位置**：cell 绕原点 grow 后，质心的分数坐标从 `f_c` 变成 `f_c / scale`。`amplitude = 0.1`、质心在 (10,10,10) Å 时，相当于把整团簇相对盒子平移约 1 Å——这个平移不属于任何应变张量，而且随"盒子原点选在哪"改变。

**建议。** positions 与 cell 用同一个仿射映射：`positions * scale`（即 `F = sI`、`u_affine = (F − I) r`），分数坐标精确不变。这正是 MD 里均匀应变的教科书做法，也让"同一结构在盒子里整体平移后再做应变 → 响应曲线相同"成为可测不变量。若担心大位移下原子被推出原胞，正解是保持分数坐标不变（本方案），而不是只把 positions 挪到质心。

**后果分级（诚实说明未验证部分）。** 对严格平移+旋转不变、且不依赖盒长做截断的描述符，两种写法数值相同——差别只在 (a) 含分数坐标/盒长信息的特征、(b) 未周期性包裹的坐标进入近邻/镜像处理的路径、(c) 结果对原点的可依赖性。我在写这份提案时只做了代码与代数核对，**没有**实测 (b) 在具体 adapter 里的影响，批准后会连同测试一起验证。

**测试缺口。** 目前 strain 只有参数校验（`job_runner.py:361-362,383-384`），`tests/test_analysis_ipc.py` 的扰动用例只跑 jitter。批准后加三条：分数坐标不变、原子间距离 == 基线 × scale、整体平移结构后响应曲线逐点相等。

**影响面。** 后端：`job_runner.py:453-462` 两行。前端无改动。

**失效。** 改变 strain 灵敏度产物 → 需要 bump。

---

## 附：另两项，我判断是 bug 而非口径（一并请示）

这两条同样改变数值，但"什么是对"没有争议，不需要你定调，只需要一个 go：

- **P1-13 轨迹事件阈值在 MAD==0 时退化**（`_common.py:246-255`，无 `robust_sigma <= 0` 保护）。每帧记两次 / 描述符量化 / MC 拒绝帧 → median=0、MAD=0、threshold=0、`event_rate ≈ 0.496`，且所有 `threshold_ratio` 因 `event_threshold > 0` 不成立而变 `None`。建议：`robust_sigma` 非正时回退 `mean + sensitivity * std`，并在 warning 里说明回退原因。
- **P1-18 FPS 用展开式平方距离**（`sampling/fps.py:205,216` 的 `|a|²+|b|²−2ab`），而同层 `_common.py:388-392` 明确注释拒绝该恒等式（cancellation），`tests/test_umap_numpy.py:79-93` 还把这条不变量钉成了测试。实测 n=400、d=96、公共偏移 1e6、散布 1e-3：`argmax` 选中真值 1.8e-4 而非最大 3.0e-4，中位相对误差 80%，400 行排名全翻转。默认 `robust` 缩放会掩盖它，但 `scaling:"raw"` 是 UI 选项（`restore.ts:98`）。建议：改用分块显式差值，和 `_common.py` 同一实现。

（P1-19 `_aligned_space_metrics` 的自排除已经在第六批修掉了，不在此列。）

---

## 版本与失效策略

缓存身份是 `sha256(analysis_type + inputs + canonical_params + ANALYSIS_ALGORITHM_VERSION)`（`services/artifact_service.py:224-237`）。

1. **一次 bump**：`services/analysis_helpers.py:28` 的 `"studio-analysis-4"` → `"studio-analysis-5"`，与批准项在同一个 commit。每项各 bump 一次会换来多次无谓的全量重算。
2. **历史行不会被删、也不会被标 STALE**：bump 只让旧 `cache_key` 不再命中，同参数再提交会新算一行；`analysis_runs.algorithm_version`（`storage/database.py:91`）可区分新旧，`artifact_service.py:193-195` 让历史产物继续可读（审计与对比用）。磁盘产物零丢失，代价是"重算一遍"。
3. **不要动 `FINGERPRINT_VERSION`**：那是描述符/数据集侧的等价杠杆，bump 它会让所有 descriptor run 失效，代价大一个量级；本提案五项全在分析层。
4. **口径 4 按推荐实现时不产生失效**，因此我建议它单独一个 commit、放在 bump 之前落地，先拿到 `selection_scores` 的前端降级路径。
5. 每项都要在 `tests/test_analysis_engine.py` 里钉一条断言（这是第 4 步原本的要求），并且金标 keys 文件重生成一次。

## 请逐条批

- [ ] 口径 1：coverage 默认改 `standardized` + 生效尺度回写 preview + 进缓存身份 —— **建议批准**
- [ ] 口径 2：零方差判据改为相对量级（1e-12×max(|mean|,1)）、六处共用一个 helper —— **建议批准**（float32 量化列不在本次覆盖范围，见正文）
- [ ] 口径 3：`coordination = len(contacts)`，截断只作用于邻居行，preview 记 `coordination_truncated_atoms`，`Explore.tsx:783` 改读数组 —— **建议批准**
- [ ] 口径 4：新增 `selection_scores` + `pool_mask`，`scores` 保留并标注 `score_semantics: "final_state"` —— **建议按"先加不改"批准**
- [ ] 口径 5：strain 的 positions 与 cell 一律绕原点 —— **建议批准**
- [ ] 附录两项（P1-13 MAD 退化、P1-18 FPS 距离）—— **建议一并批准**

批准后我按 4 → 1 → 2 → 3 → 5 → 附录 的顺序做（口径 4 不失效所以先走），最后一次性 bump 并跑 pytest / vitest / lint / tsc / Playwright / cargo。
