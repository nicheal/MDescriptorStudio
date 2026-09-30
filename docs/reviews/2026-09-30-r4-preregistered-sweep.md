# R4 预注册重跑结果(2026-09-30)

- 场景:`benchmark/config.json`(预注册,7 组 × 20 seeds × 10000 evals,seed 1000–1019,carbon `ds_d56748fb4391` fingerprint `v4:446a981b…`,NEP `run_57a8b8c40286`,selection_strategy=structure_fps_v1)
- 结果目录:`benchmark/results/20260929T043150Z/`(140/140,`run_results.jsonl` + `SHA256SUMS`);经历一次夜间中断,由 `benchmark/resume_sweep.py` 从检查点补齐 41 个 run
- 引用版本:gen-4 / harness 2026-09-29(P0-03/04 修复后)/ 策略 structure_fps_v1(见 `docs/generation_verification_matrix.md`)
- 主指标:`unique_per_100_evals`(严格唯一局域环境数 / 100 次评估,阈值 0.25 robust-scaled);配对差 vs random,bootstrap 95% CI(10k 重采样,固定 RNG)

## 1. 主指标:严格唯一环境发现 / 100 评估

| 组 | mean | median | sd | min–max |
|---|---|---|---|---|
| random | 8.42 | 8.30 | 0.60 | 7.61–9.64 |
| random-reuse | 7.98 | 7.26 | 1.89 | 5.40–12.60 |
| genetic | 11.91 | 12.18 | 3.52 | 4.17–19.30 |
| pso | 13.33 | 13.78 | 5.71 | 5.20–26.37 |
| target_region | 12.14 | 12.60 | 1.74 | 9.31–15.70 |
| genetic-target | 10.01 | 9.41 | 1.98 | 7.78–15.61 |
| pso-target | 14.02 | 13.57 | 3.10 | 8.13–21.79 |

### 配对差 vs random(正 = 发现更好)

| 组 | mean diff | 95% CI | 胜率 |
|---|---|---|---|
| random-reuse | −0.45 | [−1.18, +0.34] | 6/20 |
| genetic | **+3.49** | [+1.79, +5.08] | 16/20 |
| pso | **+4.90** | [+2.52, +7.37] | 15/20 |
| target_region | **+3.72** | [+2.92, +4.54] | 20/20 |
| genetic-target | +1.59 | [+0.70, +2.60] | 15/20 |
| pso-target | **+5.60** | [+4.31, +7.01] | 19/20 |

交叉配对:pso − genetic +1.42 [−1.81, +4.73](平);pso-target − target_region +1.87 [+0.46, +3.29] 16/20;pso-target − genetic +2.11 [−0.15, +4.28](平)。

## 2. 定向质量(proximity,robust-scaled 单位)

| 组 | 中位数的中位 | mean within_r15 |
|---|---|---|
| target_region | **25.26** | **0.216** |
| genetic-target | 26.44 | 0.225 |
| random | 47.50 | 0.055 |
| pso-target | 54.41 | 0.162 |
| random-reuse | 98.29 | 0.029 |

## 3. 次要指标

| 组 | coverage radius | accepted | wall |
|---|---|---|---|
| random | 36.38 | 177.5 | 332s |
| random-reuse | 38.04 | 218.9 | 235s |
| genetic | 39.47 | 192.5 | 266s |
| pso | 41.67 | 222.1 | 336s |
| target_region | 42.68 | 211.0 | 318s |
| genetic-target | 41.54 | 194.1 | 421s |
| pso-target | 43.21 | 208.2 | 436s |

## 4. 与存档记录的关系(结论反转)

| 存档结论(旧口径) | 本次重跑(修正口径) |
|---|---|
| GA 输给 random(0/20,−6.45/100) | **GA +3.49 [+1.79, +5.08],16/20 胜** |
| PSO ≈ GA、无增益、不推荐 | PSO +4.90 [+2.52, +7.37],15/20;方差仍最高 |
| random-reuse 惨败(0/20,−13.89) | 统计平局 −0.45 [−1.18, +0.34];coverage 也不再最优 |
| TR 的 coverage 比 random 差 | TR coverage 42.68 > random 36.38 |
| TR proximity 25.50(20/20) | 25.26(复现良好 ✓) |

反转归因:P0-02 严格唯一计数(旧计数器重复计数与"非新颖行阻断"两个缺陷方向性不对称)、P0-03(旧 harness 的 `random` 标签带锚点跑的是定向分支)、P0-01 算子参数修复。这正是验证矩阵禁止新旧数值混排、要求版本化引用的原因;存档记录按"历史记录"保留。

## 5. 审计门控动作(R4/R5)

- **R4 门控达成条件(单数据集)**:GA/PSO 在修正口径下有稳定实证改进 → 按 R5.6 更新 GUI 中 GA/PSO/reuse 的实测提示文本(本次提交同步)。**默认切换与 crossover/NSGA-II 仍不启动**:审计要求"增加另一类结构或多组分材料"后再决定,单 carbon 数据集不构成默认策略变更的依据。
- 尚未执行:两种选择策略(structure_fps_v1 vs local_incremental_maximin_v1)的真实数据同预算对比与 perf/RSS 测量(需另一次预注册 sweep);多组分/第二材料基准;R5 其余产品化项。

## 6. 复现

`python benchmark/genetic_vs_random.py --config benchmark/config.json`(约 12–14 小时;中断后 `python benchmark/resume_sweep.py --config benchmark/config.json --results-dir benchmark/results/<utc>`)。逐 seed 数据 + 校验和在 `benchmark/results/20260929T043150Z/`(gitignored;P1-06 数据发布决策待定)。
