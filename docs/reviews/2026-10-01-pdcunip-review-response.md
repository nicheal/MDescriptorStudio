# PdCuNiP 预注册外部审阅——核实与修复记录（2026-10-01）

- 审阅对象：外部审阅报告（基于 GitHub 上的 main `8ed4bdb`，只有仓库访问权限），主题为 `benchmark/config.pdcunip.json` 与已发布 carbon R4 的可比性。
- 本文记录：本地逐项核实结果（审阅方声明"无法核实"的部分用本机数据补齐）、已落地的修复、以及留给用户的运行决策。
- 工作区状态：本批次与同日的并行 R5.1 外部审计修复批次（`docs/reviews/2026-10-01-r51-audit-fixes.md`）共存于同一工作区，全量后端套件在两批合并下通过。

## 1. 审阅三项"已确认问题"的核实结论

### 1.1 新旧主指标计数口径不同 —— 属实，已用口径戳关闭混排通道

- 属实：已发布 carbon R4（`results/20260929T043150Z`，`published/2026-09-30-r4/`）产生于 782b531（缩放空间计数修复）**之前**的进程，unique 为 raw 空间口径；当前引擎为 scaled 空间口径；两份配置的 `metric_definition` 文本相同，仅看配置确会误判可比。
- 修复：harness 新增 `METRIC_CALIBER = "strict-unique-scaled"` 常量与 `HARNESS_VERSION = "2026-10-01"`；预注册必须声明 `metric_caliber` 且加载时强校验（不符即 SystemExit）；`environment.json` 携带 harness/口径/算法版本戳；`config.json` 注记明确其**已发布**运行是 `strict-unique-raw` 口径。历史 R4 数值与修复后运行不得混排（矩阵 §1/§4）。
- **连带发现（审阅未提）**：正在运行的 selection-strategy sweep（20260930T101403Z）为 scaled 口径，而其配对基线 20260929T043150Z 为 raw 口径——**该 sweep 完成后的 local-vs-fps 配对 unique 差值混杂了"选择策略"与"计数口径"两个因子**。`config.local-selection.json` 注记已强制要求引用时带口径注解；策略层结论应先在修复后代码上重跑 fps 基线（见 §4 决策）。

### 1.2 恢复路径混入 carbon —— 属实，已修复并加三重护栏

属实（`resume_sweep.py` 旧实现调用 `_load_run_config()` 时读模块默认 ID ds_d56748fb4391/run_57a8b8c40286，从不应用预注册 ID；summary.json 的 dataset/run 标签同样取默认值）。修复：

1. 新增 `apply_preregistration(config)`：主 harness 与 resume 统一经它解析材料绑定，resume 在启动任何补跑前注入预注册 ID。
2. `_verify_used_config`：恢复前比对目录内冻结的 `config.used.json`（后加的注记键与 note 除外），不一致即拒绝。
3. `_verify_row_identities`：逐 run 行新增 `dataset_id`/`descriptor_run_id`；恢复时逐行核验，异材料行拒绝；2026-10-01 之前无身份的旧行仅对默认 carbon 配置可续（旧代码只可能产出 carbon 行）。
4. 附带：`_load_run_config` 现校验 run.dataset_id == 预注册 dataset、结果 `metadata.json` 的 dataset_fingerprint == dataset 当前指纹（对齐应用的提交/worker 门）；工件写盘显式 LF。
5. 汇总补齐：`summary` 现含 unique/100 的 p90、wall_seconds 与 peak_rss_mb 的 mean/median/stdev/p90。

### 1.3 预注册校验不约束完整合同 —— 属实，已按完整合同重写

审阅列举的五个绕过样例现全部在加载时被拒（`_load_preregistration` 重写）：`primary_metric="wall_seconds"`、`algorithm_version="unsupported"`、`selection_strategy="invalid"`、`repeats=0`、`groups=["nonsense"]`。此外新增校验：metric_caliber、harness_min_version ≤ 当前版本、primary_groups ⊆ groups、secondary_metrics ⊆ 已知指标、seed_base/budget 整型下界、max_accepted/max_generations 与 harness 常量一致、dataset_id/descriptor_run_id/preregistered_at/metric_definition 存在。测试：`TestPreregistrationContract`（13 项）。

## 2. 审阅方"无法核实"部分的本地核实（全部通过）

审阅方只有 GitHub 访问权限，声明数据绑定、特征维度、锚点性质无法核实。本机核实结果：

| 核实项 | 结果 |
|---|---|
| run 属于数据集 | `run_644f6186340c.dataset_id == ds_9ca89d14f8f0` ✓（carbon 同） |
| 指纹新鲜度 | 结果 `metadata.json` 的 dataset_fingerprint == datasets 表当前指纹 ✓ |
| 指纹重算（文件未变） | `compute_fingerprint` 重算 == 注册指纹 ✓（PdCuNiP.xyz 未入库，此核实尤其重要） |
| values.npy | [736798,35]，全有限 ✓；carbon [379404,35] ✓ |
| row_offsets | 单调、首 0、末端 == 行数、帧数 == 数据集帧数 ✓ |
| 零方差特征 | 两材料原子级/结构级均无 ✓ |
| 每帧原子数 | PdCuNiP 中位 106、范围 2-256（**配置注记原写 "~54-100 atoms" 有误，已更正**）；carbon 2-512 |
| 原子级阈值 0.25 响应度 | 两材料采样 NN<0.25 比例 ≈0.6-0.8%，中位 NN 4.96/2.60——不饱和不近零 ✓ |
| 元素支持 | run 已在该数据集上 COMPLETED（Cu/Ni/P/Pd 全部计算出原子行），由结果本身佐证 |

**元数据不一致（记录在案）**：两次描述符运行的 DB 元数据 engine_version 不一致（carbon 0.3.3 vs PdCuNiP 0.2.3），与实际 wheel 版本演进史不符，疑为早期注册路径的填写差异；两运行的特征维度/语义/完成状态一致。引用时以各自 `environment.json` 与结果 metadata 为准；如需模型哈希级可比性，须将来在描述符运行元数据中固定模型文件哈希（引擎侧改进，未排期）。

## 3. 锚点核实——审阅的怀疑被证实且更严重，已按既定流程换锚

审阅指出"相同索引不代表相同结构角色，harness 也没有锚点几何预检"。本机核实（对 PdCuNiP 参考矩阵 + 几何预检）：

- **1322 在 PdCuNiP 上违反运行几何约束**（存在低于共价最小距离的接触）——应用 worker 会直接拒绝该 run；harness 中则会让定向组浪费提案（carbon 上同索引合法）。
- **5075 在 PdCuNiP 缩放空间落在数据密集核心**：r=15 邻域内 7719/9615 帧（carbon 同索引仅 21/6738）——"目标区域"实为全域，与 carbon 的锚点角色完全不等价。
- 换锚（按 carbon 既定流程"最具区分度的几何合法帧"，并要求体相代表性、互相远离）：**2256 与 2133**（各 54 原子，最近云帧 27.2/27.0 缩放单位，互相 >40；最孤立的合法帧 2910 为 2 原子二聚体、865 为 12 原子团簇，均因代表性不足排除）。`config.pdcunip.json` 已更新并在注记中记录证据链；harness `run_once` 现在锚点越界/违反几何约束时与 worker 同款 fail fast（此 sweep 若按旧配置启动，会在第一轮就被新预检拦下）。
- **r=15 与扰动步长的可迁移性**：同一探针代码下，单次 displacement（σ≤0.15 Å）的描述符位移 carbon 1.1-9.1、PdCuNiP 0.65-8.7 缩放单位——量级相当，r=15 > 步长的设计在两材料均成立（历史 13-18 标定值与本次探针协议不同，但材料间可比性以同批探针为准）。
- 锚点对距离：96.18 缩放单位（PdCuNiP，两区域彼此独立）。

## 4. 留给用户的决策（未擅自执行）

1. **修复后 carbon fps 基线重跑**（`--config benchmark/config.json`，~13h）：同时解决 (a) selection sweep 完成后 local-vs-fps 的口径混杂，(b) §1.1 的"重建修复后 carbon 对照"。建议在 selection sweep（当前 84/140）完成后启动；PdCuNiP 内部配对（random/GA/PSO 同代码同口径）不依赖它，可在其后或并行排队。
2. **PdCuNiP sweep 启动**：配置已就绪（`--config benchmark/config.pdcunip.json`），锚点已换、合同已冻结。
3. **发布包换行重整（下次提交时一次动作）**：git 存的是 LF、SHA256SUMS 按 CRLF 计算，非 Windows checkout 校验会失败。已加 `.gitattributes`（`benchmark/published/** -text`）；**下次提交时须 `git add benchmark/published/` 让包以 CRLF 原字节重新入库**，此后任何平台 checkout 均可按清单校验。
4. mdescriptor wheel 升级至 0.3.5（并行批次已把 requirements 下限升为 0.3.5）仍按原计划等 sweep 结束后执行（Windows DLL 锁）。

## 5. 本批次改动清单

- `benchmark/genetic_vs_random.py`：METRIC_CALIBER/HARNESS_VERSION/MAX_ACCEPTED/MAX_GENERATIONS/KNOWN_* 常量；`_load_preregistration` 完整合同；`_load_run_config(dataset_id, run_id)` 绑定/指纹门；`apply_preregistration`；`run_once` 行内材料身份 + 锚点几何预检；`_summarise` 补 p90/wall/RSS；`_environment_facts` 版本戳；工件 LF 写盘。
- `benchmark/resume_sweep.py`：重写（材料护栏三重 + LF + summary 标签取自预注册）。
- `benchmark/config.pdcunip.json`：锚点 2256/2133、metric_caliber、max_accepted/max_generations、peak_rss_mb 注册、注记更正与分析合同。
- `benchmark/config.json`、`config.local-selection.json`：metric_caliber 戳 + 口径注记（后者明示跨 sweep 配对的口径混杂）。
- `tests/test_benchmark_harness.py`：11 → 30 项（预注册合同 13 项、resume 材料护栏 5 项、PdCuNiP 配置锚定等）。
- `.gitattributes`（新增）：发布包 `-text`。
- `docs/generation_verification_matrix.md`：§1 增计数口径字段行 + harness 2026-10-01；§3 增 PdCuNiP 绑定/锚点/探针事实；§4 增 2026-10-01 外部审阅批次条目。
- 测试证据：`test_benchmark_harness.py` 30/30；全量后端 697 passed + 5 skipped + 5 个既有环境失败（hdbscan/analysis_ipc/backend_smoke/descriptor_flow×2，与干净 HEAD 一致，非回归）。
