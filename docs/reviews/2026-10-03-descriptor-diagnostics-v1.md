# 2026-10-03 外部审查响应 —— Descriptor Diagnostics V1

- 审查对象:`main` @ `e81c992`(外部审查报告:MLIP descriptor diagnostics / descriptor-guided dataset construction 对照)
- 本文档:审查 P0"描述符正确性验证"缺口的第一期落地记录(算法层 + RPC + 科学不变量测试),以及关键设计决策与已知边界
- 结论先行:**V1 覆盖审查五项核心诊断(formal invariance / cutoff smoothness / per-environment Jacobian SVD / degeneracy search / distance consistency)的后端算法层,全部为纯 numpy 引擎无关实现,经玩具描述符解析性质验证 + 真实引擎冒烟**;前端面板、TwoNN、等变描述符 W_l(R) 校验、MLIP ensemble uncertainty、task validation 留待后续批次

## 1. 审查论断与本轮落地对照

| 审查要求(§2/§18) | V1 状态 | 位置 |
|---|---|---|
| 正式描述符正确性验证:平移/旋转/同种置换/镜像 + fp32/64 对照 | ✅ `formal_invariance`(五项检查,逐结构 ε 统计 + 手性敏感单独判读) | `analysis/diagnostics/formal.py` |
| cutoff 连续性:r=rcut±δ 扫描,输出 D(r)、dD/dr、d²D/dr² | ✅ `cutoff_smoothness`(重建描述符扫描 + 响应曲线/导数范数 + 截断处跳变比) | `analysis/diagnostics/cutoff.py` |
| per-environment Jacobian rank analysis(expected_rank = 3n_neighbor − 3) | ✅ `environment_jacobian`(中心原子固定、有限差分、旋转零模投影、SVD、逐原子 rank/条件数/近零模) | `analysis/diagnostics/jacobian.py` |
| descriptor degeneracy search(d_D 小而 d_structure/ΔE 大) | ✅ `degeneracy_search`(描述符空间 kNN 候选对 → 结构/能量判据 → 危险对表) | `analysis/diagnostics/degeneracy.py` |
| distance consistency(d_D vs RMSD/ΔE/ΔF,d_D≪1 ∧ ΔE≫1 危险区) | ✅ `distance_consistency`(Pearson/Spearman + 分位箱校准曲线 + collapse 富集比 + 危险对表) | `analysis/diagnostics/consistency.py` |
| "新建 `analysis/diagnostics/`,不要塞进 metrics/_common" | ✅ 独立包,五个模块 + geometry 共享层;算法层零引擎依赖 | `analysis/diagnostics/` |

接线:`analysis/registry.py` 注册五个 `diagnostics` 类目 RPC 包装;`services/job_runner.py::_run_diagnostics` 按 perturbation runner 同款契约持有帧与重算闭包;`main.py` 经 registry 名单自动暴露 `analysis.formal_invariance` 等 5 个 RPC(`submit_generic` 通道,缓存键与 artifact 契约复用通用层)。

## 2. 关键设计决策(与审查文本的差异点均已给出理由)

1. **重算契约**:`DescriptorRecompute{structure_values, atomic_values, row_offsets}`(与 `generation/evaluator.py::DescriptorEvaluation` 同构,analysis 侧独立定义以保持 diagnostics 包无 generation 依赖)。服务层闭包 = `to_structure_batch → adapter.compute → evaluate_batch`,与 perturbation runner 完全同源。
2. **重算驱动诊断的基线 = 新算而非存储矩阵**:invariance/jacobian 的全部比较都在同一次重算路径内部完成(同 descriptor 实例、同 device),存储矩阵只用于 cutoff 的"重建 ≠ 存储"交叉告警(参数名覆盖错误时报警而非污染曲线)。
3. **fp32/64 对照的解释**:V1 实现为输入坐标经 float32 往返后的响应偏差(数值精度敏感性);描述符内部计算的精度切换属于引擎能力,V1 不做。
4. **Jacobian 期望秩**:按审查公式 `3·n_neighbor − 3`(中心原子固定,旋转是仅有的精确零模)。旋转零模投影前过滤范数为零的生成方向(共线构型某轴生成器退化为零向量,直接 QR 会把任意正交补也投掉而少算秩)。同时报告 `effective_neighbor_count`(Jacobian 实际有响应的邻居数)——分析 cutoff 超过描述符真实 cutoff 时给警告提示,避免把 cutoff 配错误读为不完备。
5. **结构距离指纹**(degeneracy/consistency 的地面真值):
   - structure 粒度 = 最小镜像成对距离排序向量 L2 + 组分 L1(`composition_weight`,默认 1);**原子数不同 = 结构距离 inf**(如实反映"不同构成的配对结构上不可比",标记 `atom_count` 理由);
   - atom 粒度 = 中心距离排序向量(定长 padding)L2 + 中心物种失配(0/1)+ 邻居组分 L1(`species_weight`,默认 1);
   - **已知局限:排序成对距离对手性不可见**——对映体对的 d_struct = 0,该退化对此指纹天然盲(审查文档同样提示 RMSD 类指纹需对齐;V2 计划加手性指数项)。算法层地面真值是注入的 callable,可替换更强指纹。
6. **描述符距离度量 = raw**(不经 standardized):退化/一致性问的是描述符自身度量下的坍缩,审查口径即 d_D 的本征空间;与 Analysis 主套件默认 standardized 不同,这是有意的、已在模块 docstring 声明的选择。
7. **cutoff 跳变度量**:δ=0 两侧相邻步长变化 vs 网格内部平均步长之比(默认阈值 3)。初版"±δ1 straddle vs 内部 straddle"度量对阶跃函数失效(内部 straddle 同样为 O(1)),已废弃。
8. **算力上限**(全部显式参数,防单请求无界扫描):formal/cutoff `max_structures`(默认 64/32,cap 512/128);jacobian `max_structures`(默认 2, cap 16)+ `max_atoms`(默认 32, cap 64,超限结构跳过并告警——displaced 拷贝缓冲与批量行数都按 natoms²×F 增长);degeneracy `max_samples`(默认 1024, cap 4096,均匀抽样 + 告警);consistency `max_samples`(默认 128, cap 512,全对矩阵)。
9. **成本模型**:formal = n_frames × (2 + n_rotations + 1 + 1) 次小结构重算;jacobian = 6 × natoms 次重算/结构(批量一次提交,全部中心原子共享);cutoff = n_steps 次全帧重算(描述符按 cutoff 参数重建);degeneracy = kNN 候选对(~k·N)次懒式几何查询,指纹预计算一次。

## 3. 测试(科学不变量测试,审查 §17 清单的 diagnostics 部分)

`tests/test_analysis_diagnostics.py`,28 项:

- **形式不变性**:不变玩具描述符(排序成对距离)五项检查全过(精确对称 ε<1e-9,precision ε<1e-6);故意非不变描述符(z 求和)被 translation/rotation/reflection 捕获而 permutation 通过;手性描述符(定标签符号体积)被判 `chirality_sensitive` 而非一般失败;atom 粒度逐原子行置换对齐校验。
- **Jacobian 秩期望**:完备玩具描述符(n+1 点全距离集)在一般构型下 5 个原子全部 `observed_rank == expected_rank == 9`(3·4−3)且旋转残差 < 1e-6;不完备玩具(仅中心距离,4 特征)deficiency ≥ 5 全部检出;共线构型秩坍缩被检出(deficiency ≥ 2);缺 atomic rows / 缺 cutoff 明确报错。
- **cutoff**:对恰好落在 rcut 上的原子对,跳变比触发 flag;远离 rcut 时全网格平滑;重建-存储失配告警;偶数网格拒绝。
- **退化搜索**:描述符孪生对(d_D≈0,d_struct=10)以 reason=structure 入危险对表;inf(原子数不同)以 reason=atom_count 报告;能量信号(ΔE 大)独立触发;样本不足报错。
- **距离一致性**:线性关系 Pearson>0.99;植入坍缩对后 collapse 富集比超独立基线且危险对表命中;矩阵形状失配报错。
- **真实引擎冒烟**:枚举引擎描述符注册表,取首个可构建者对周期混种小体系跑不变性电池(translation/rotation/permutation/precision 全过才放行;引擎缺失 skip)。

## 4. 前端 UI(同日第二批,已落地)

Analysis 页新增 **"描述符诊断"导航组**,五个模块端到端可用(参数卡 → `analysis.<name>` RPC → 结果面板):

- **形式不变性**:判定指标条(通过/失败 + 手性敏感警示条)+ 五项检查表(最差 ε/均值 ε/失败数/判定)+ 原理说明;
- **截断平滑性**:响应曲线图(全部结构均值线 + 被标记结构红色高亮,rcut 处虚线定位,y 轴对数)+ 被标记帧表;
- **环境雅可比秩**:逐原子期望秩 vs 观测秩分组柱状(前 60 原子)+ 秩亏直方图 + 最差原子表(结构/原子/物种/邻居数/条件数/旋转残差/判定)+ 秩亏警示条;
- **退化搜索**:候选对散点(d_D vs d_struct,有能量时按 |ΔE| Viridis 着色)+ 危险对表(理由中文化:结构不同/原子数不同/能量不同/结构+能量);
- **距离一致性**:全体配对散点 + 分箱均值/P90 校准曲线叠加 + collapse 富集警示条 + ΔE 相关性文字 + 危险对表。

接线清单(照仓库惯例逐处注册):`types.ts`(OverviewAnalysis/AnalysisModuleKey/AnalysisParams +16 参数)、`registry.ts`(新导航组/别名/ARTIFACT_ARRAYS)、`navigation.ts` 白名单、`submission.ts`、`useAnalysisParameters.ts`、`AnalysisModuleControls.tsx`(5 控件块)、`restore.ts`(5 restore 分支)、`analysisVisualizations.tsx`(TITLES + dispatcher + 5 视图)、`jobs.ts`(5 job 标签)、`analysisMethodGuides.ts`(5 份方法指南,双语)、`zh.ts`(+88 key)、`preview.tsx`(mock:提交路由/COUNT_PARAMS/预览分支)。

验证:tsc/eslint 干净;vitest 252/252(submission/restore/identity 钉住测试覆盖全部 5 模块参数映射与恢复);e2e 62/62(新增 5 个诊断 spec + 导航清单更新至 7 组 23 模块);后端 `test_analysis_ipc.py` catalog 扩至真跑 5 个诊断方法(ACE 描述符,断言 preview 契约与 manifest 数组),`test_mock_backend_vocabulary.py` 通过。

## 5. imported run 修复(同日第三批)

用户实测即踩:全部 5 个诊断报 DESCRIPTOR_CONFIGURATION_ERROR。根因——`_run_diagnostics` 把 run 行的 `device` 原样传给 `engine.build`,而**结果导入的 run**(result_transfer_service 写入 `device="imported"`)不是执行设备,引擎报 `execution device must be exactly 'cpu' or 'cuda'`;且该 build 原先无条件执行,连不需要重算的 pair-search 诊断也被连累。修复:

- `_run_diagnostics`:描述符实例只为三个重算驱动诊断构建;imported/external run 上重算诊断在 runner 边界即报 `RESULT_INCOMPATIBLE`(文案指路 pair-search 诊断),pair-search 诊断照常运行(只读存储矩阵 + 数据集几何);
- `_run_perturbation_sensitivity`:同款守卫(修复同一潜伏问题——扰动响应对 imported run 原先也会报引擎配置错误);
- `submit_generic`:Run 点击即拒绝(imported run × 重算诊断/扰动响应),job 行不再产生;
- 回归:`tests/test_diagnostics_imported_runs.py` 7 项(imported run 构造经 ResultTransferService,断言 pair-search 可跑、重算诊断/扰动拒绝、提交期拒绝且零 job 行);并在用户真实 DPA4C imported run 上回放验证——degeneracy_search/distance_consistency 正常出结果,三个重算诊断给出明确错误。

## 6. 边界与后续(未决事项,按审查排序)

- **effective_dimension 命名/TwoNN**(审查 P1 §3):未动。改名涉及 API/前端/i18n 联动,与 Statistical Diagnostics V2(TwoNN、information imbalance、neighborhood preservation)一并做。
- **等变描述符**:V1 只校验不变族;D_l(Rx) ≈ W_l(R) D_l(x) 的等变校验待接入等变描述符时加(检查项已在 checks 枚举之外独立可扩展)。
- **ΔF 信号**:consistency/degeneracy 的能量项用逐结构 energy_per_atom;逐原子力差分需要 per-atom 力随行存储,V2。
- **sweep 门控注记**:本轮全部为 analysis 侧新增,未触碰 generation 引擎/指标口径(gen-4 不动、RNG 不动、快照/缓存键不变),符合 2026-10-02 改进计划 §4 的 sweep 期间约束;E2–E4(gen-5 版本提升)仍等 PdCuNiP sweep(`20261002T005922Z`)结束。
