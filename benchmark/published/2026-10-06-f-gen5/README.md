# F — gen-5 冻结重基准 · 发表级汇总包

Milestone F(docs/reviews/2026-10-02-improvement-plan.md)的发表级数据包:
同一冻结 R4 场景(20 repeats × 7 组 × 10k 评估,structure_fps_v1,新意阈值
0.25,geometry precheck)在两个材料上按 `GENERATION_ALGORITHM_VERSION =
"gen-5"`、harness `2026-10-03` 重新注册并重跑。每个材料一个自含子目录:

- `carbon/` — 单质碳,results dir `20261004T031301Z`(2026-10-04 启动,
  2026-10-05 07:53 连续收尾)
- `pdcunip/` — Pd/Cu/Ni/P 多组分,results dir `20261004T031310Z`(同日启动,
  2026-10-05 夜间进程外部中断一次,缺失的 seed 1019 共 6 个 run 由
  `benchmark/resume_sweep.py` 于 2026-10-06 从检查点补齐)

每个子目录含:

- `config.frozen.json` — 冻结预注册(= 仓库 `benchmark/config.gen5-*.json`,
  注册于任何重跑之前)
- `run_results.jsonl` — 140 行逐 seed 完整记录(含逐轮收敛数据;gen-5 新增
  逐轮 `strict_unique_v2` / `archived_strict_unique_v2` / `screening_*` 字段)
- `summary.json` / `environment.json` — 汇总与环境(Python 3.12.9 / 32 核 /
  numpy 1.26.4 / scipy 1.15.3 / mdescriptor 0.2.3,metric_caliber
  strict-unique-scaled)
- `SHA256SUMS` — 对本子目录全部文件(LF 行尾;自包根运行
  `sha256sum -c carbon/SHA256SUMS` 即可校验)

## 指标契约

- **主指标**:`strict_unique_v2_per_100_evals` — 排列不变的 canonical-greedy
  严格新环境计数(候选按稳定 id、原子行按字典序规范化后去重)。
- **副指标**:双口径并存 — gen-4 访问序 `unique_per_100_evals`、筛选后
  `archived_unique_per_100_evals`(本包两 sweep 均未启用筛选,三者相等;
  管线已就绪,筛选预注册可直接复用),以及 coverage / accepted / wall / rss。

## 可复现内容

- **配对分析**:仅需本包即可完整重算 — 主指标逐 seed 配对差(组内 vs
  random)、胜率、coverage/proximity 汇总,见
  `docs/reviews/2026-10-04-f-gen5-preregistrations.md` 的两张结果表。
- **确定性互证(bit-exact 审计)**:两 sweep 与同代码 gen-4 存档 sweep
  (`20261002T010733Z` / `20261002T005922Z`,仓库 results 目录)140 对
  (optimizer, seed) 逐行比对,共享 schema 全部逐位一致(含逐轮共享字段);
  gen-5 相对 gen-4 仅新增字段、轨迹不变 — 该审计依赖仓库内存档,不在包内
  重放范围,结论已记录于 F 记录文档。
- **v1 ↔ v2 口径差**:carbon 6/140 行、PdCuNiP 3/140 行,|Δ| ≤ 0.02/100,
  组排名两口径完全相同 — 真实数据上排列序效应可忽略。

## 关键结论

- carbon(fps):pso-target 最优(+44.35±27.51,18/20),pso 次之(+27.78,
  高方差),random-reuse 毒药(−45.55,0/20)。
- PdCuNiP(fps):random-reuse 最优(+27.75±21.21,18/20),genetic 次之
  (+11.56,17/20)且 coverage 最优(240.19 vs random 407.28);各定向组
  发现代价显著(TR −30.40,proximity 33.35 仍最优)。
- **组排名不随材料迁移**(carbon 与 PdCuNiP 的排序几乎整体互换)— 无普适
  优化器默认值;新材料的正确路径是复用本包的冻结预注册重跑同一 sweep。

## 数据集获取方式

数据集未随包公开;完整复现(描述符重算)需要:

- **carbon**:`ds_d56748fb4391`(6738 帧 extxyz)与 NEP 描述符运行
  `run_57a8b8c40286` 的模型/参数。
- **PdCuNiP**:`ds_9ca89d14f8f0`(9615 帧 extxyz,多组分 Pd/Cu/Ni/P,原子数
  中位 106、范围 2–256)与 NEP 描述符运行 `run_644f6186340c`。

取得说明请联系仓库作者;数据集指纹(完整值)见
`docs/generation_verification_matrix.md` §3,亦可用
`benchmark/results/*/run_results.jsonl` 行内 `dataset_id` /
`descriptor_run_id` 交叉核对。
