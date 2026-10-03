# Selection-strategy sweep (local_incremental_maximin_v1 vs structure_fps_v1) — record

Date: 2026-10-02 (sweep ran 2026-09-30 10:14 → 2026-10-01 ~23:00, 140/140 rows).
Local sweep: `benchmark/results/20260930T101403Z/` (all 7 groups × 20 seeds × 10k evals,
selection_strategy=local_incremental_maximin_v1, metric_caliber strict-unique-scaled,
dataset carbon ds_d56748fb4391 / run_57a8b8c40286, config `benchmark/config.local-selection.json`).
FPS baseline: `benchmark/results/20260929T043150Z/` (structure_fps_v1, **strict-unique-raw
caliber** — pre-782b531 code). Analysis script: `tmp/analyze_selection_sweep.py`.

## 口径警告（必须随数字引用）

- **跨 sweep 的 unique_per_100_evals 配对差混杂了「选择策略」与「计数口径」两个因子**
  （baseline=raw、local sweep=scaled），不能读作策略效应。本记录只把跨 sweep unique
  差值作为参考列出并标注 CONFOUNDED。
- 合法比较只有两类：**同 sweep 内的组间配对**（同策略同口径）；**跨 sweep 的
  wall_seconds / accepted**（与计数口径无关）。
- baseline sweep 早于 rss 采样功能，**没有 peak_rss_mb**——内存只有单侧数字。

## 1. 同 sweep 内组间配对（local 策略，scaled 口径；vs 同 seed 的 random）

| group | unique/100 差值 (wins) | coverage 差值 (wins) | wall 差值 s (wins) | prox median | within_r |
|---|---|---|---|---|---|
| random | — (147.45±10.26) | — (41.87±1.86) | — (654.6s) | 47.82 | 3.7% |
| random-reuse | **+111.44±88.21 (20/20)** | +0.27±9.51 (10/20) | +169.3 (20/20) | 45.81 | 12.2% |
| genetic | +7.45±37.99 (8/20) | −1.21 (8/20) | +230.8 (20/20) | 46.50 | 14.1% |
| pso | −41.74±58.72 (4/20) | +1.94 (16/20 差) | −71.6 (13/20) | 59.95 | 14.3% |
| target_region | −4.13±12.23 (7/20) | +10.70±2.50 (20/20 差) | +59.1 (19/20) | **19.31** | **31.2%** |
| genetic-target | −12.38±12.36 (4/20) | +2.75 (17/20 差) | +156.2 (20/20) | **21.22** | **31.0%** |
| pso-target | −29.28±32.17 (3/20) | +3.27 (18/20 差) | +65.7 (15/20) | 52.95 | 18.6% |

绝对均值：random-reuse 258.90±84.56（**中位数仅 194.27**，重尾——少数 seed 爆发性增长）、
pso 105.71±59.74（中位 131.02，左偏重尾）、target_region 143.33±4.23（全场最稳）。

## 2. 跨 sweep 成本对比（local − fps，逐 seed 配对；口径无关，合法）

| group | wall 差 s（20/20 全部更慢） | accepted 差 | unique 差 [CONFOUNDED] |
|---|---|---|---|
| random | +322.2±48.6（654.6 vs 332.4，×2.0） | +0.0±0.0（逐 seed 完全一致——提议流/接收语义确定性互证） | +139.03±10.31 |
| random-reuse | +588.5±111.3 | +62.0±16.5 | +250.92±84.61 |
| genetic | +619.3±163.9（×3.3） | +19.2±13.1 | +143.00±39.87 |
| pso | +247.0±124.3 | −0.7±29.4 | +92.38±55.88 |
| pso-target | +284.5±82.5 | −0.3±14.0 | +104.16±29.23 |
| genetic-target | +389.9±73.4 | +8.3±10.0 | +125.06±5.94 |
| target_region | +396.0±57.4 | +13.6±7.8 | +131.18±5.40 |

peak_rss（仅 local 侧，无 baseline 对照）：946–1032 MB，组间无实质差异。

## 3. 结论

1. **local 策略下优化器排名重排，random-reuse 翻盘**：fps 时代 random-reuse 对 random
   平到负（R4: −0.45 tie），local 策略下变成 **+111.44，20/20 全胜**——局部 maximin
   选择把"已接受区域加密"的重复提议转化成了严格新环境（选择器按 marginal strictly-new
   计数挑行，重复提议不再浪费接收名额）。代价是重尾方差（中位 194 vs 均值 259）。
   这是本 sweep 最重要的新事实。
2. **random 仍是紧致基线**（147.45±10.26，全场方差最小），genetic +7.45 (8/20) 是噪声，
   pso −41.74 (4/20) 有害且双峰——GA/PSO 在 local 策略下依旧不是发现主力，与 fps 时代
   R4 结论方向一致。
3. **定向补采样结论迁移**：target_region proximity 19.31 / within_r 31.2%（vs random
   47.82 / 3.7%），discovery 只付 −4.13 (7/20 不显著)；genetic-target 21.22/31.0% 但
   discovery −12.38 (4/20) 更差；**pso-target 定向失效**（proximity 52.95 ≈ 无定向）。
   "定向补采样 = random + search target" 的推荐在 local 策略下依然成立。
4. **成本**：local 策略 wall 全组 20/20 变慢，random ×2.0、genetic ×3.3——贪心
   marginal-count 循环的代价。若预算以 wall 计，fps 仍是快路径；若以评估数计且目标是
   strict unique 指标本体，local 值得。
5. **推荐默认更新**（写入矩阵 §5 的候选措辞）：全局发现 = random + fps（快）或
   random + local（指标导向，wall ×2）；已接受区域加密 = random-reuse + local（新证据，
   20/20）；定向补采样 = random + search target（不变）。

## 4. 遗留

- ~~单材料（carbon）结论；PdCuNiP sweep（2026-10-02 启动）按同设计在第二材料复核~~ PdCuNiP sweep 进行中（20261002T005922Z，92/140 于 10-03 09:22），完成后只做同口径组内分析。
- ~~carbon fps 重跑基线（当前代码、scaled 口径）仍未拍板~~ **已拍板并完成**（用户 2026-10-02 问"任务可以并行吗"后按并行启动），去混杂结论见 §5。

## 5. 去混杂结论（2026-10-03，fps 重跑基线 20261002T010733Z 完成后）

fps 重跑基线：140/140，当前代码（f3c6dd8 后引擎 + 071ec73 预注册契约），`structure_fps_v1`，
metric_caliber = strict-unique-scaled（与 local sweep **同口径**）→ 跨 sweep unique 配对差值
**现在是合法的策略效应**。分析脚本 `tmp/analyze_deconfound.py`。

争用注记：fps 重跑基线与 PdCuNiP sweep 并行运行（09:07 起），wall 较串行 fps 历史慢
+80–143s/组（20/20）——wall 数字一律带此注记；unique/coverage/accepted 与 CPU 争用无关。
一致性互证：串行时代 random 的 local−fps wall 差 +322.2s ≈ 争用修正后的同口径估计
（+211.9 + ~110 争用）。

### 5.1 策略效应（同口径，local − fps，逐 seed 配对）

| group | unique/100 差值 (wins) | accepted 差 | wall 差 s |
|---|---|---|---|
| random | **+77.46±8.07 (20/20)** | +0.0±0.0 | +211.9（争用低估 ~110） |
| random-reuse | **+234.45±85.90 (20/20)** | +62.0 | +508.5 |
| genetic | **+95.75±54.15 (20/20)** | +19.2 | +537.9 |
| genetic-target | **+54.42±19.68 (20/20)** | +8.3 | +246.8 |
| target_region | **+71.15±14.54 (20/20)** | +13.6 | +301.3 |
| pso | +7.93±24.34 (17/20, n.s.) | −0.7 | +145.9 |
| pso-target | +3.83±21.09 (15/20, n.s.) | −0.3 | +146.9 |

绝对均值（local / fps-new）：random 147.45±10.26 / 70.00±5.95；random-reuse **258.90±84.56 /
24.45±9.97**；genetic 154.91±38.21 / 59.16±25.25；target_region 143.33±4.23 / 72.18±12.56；
pso 105.71±59.74 / 97.78±54.71；pso-target 118.18±30.60 / 114.35±27.41。

### 5.2 结论

1. **策略效应真实且巨大**：除 pso/pso-target（记忆式引导可能与局部选择做了同一件事，
   增益不显著）外，所有组 20/20 从 local 选择获益；纯 random +77.5/100。
2. **reuse×策略交互是最大单一发现**：fps 下 reuse 是毒药（24.45，组内 vs random 0/20、
   −45.55±9.16）；local 下 reuse 是最优（258.90，20/20）。「reuse is poison」是
   **fps 策略专属**结论，正式翻案。
3. **口径效应定量分解**（旧混杂 +139.03 = ？）：fps 旧 raw → 新 scaled，random +61.58
   （各组 +16.5~+100.3）→ 旧跨 sweep 差值 ≈ 口径 +61.6 ⊕ 策略 +77.5。
4. fps+scaled 下组内排名：pso-target 最优发现（114.35，vs random +44.35，18/20）、
   reuse 最差（−45.55，0/20）；local+scaled 下排名几乎反转（reuse 最优、pso 最差）——
   **选择策略改变优化器排名**，引用任何"优化器 X 优于 Y"必须同时声明策略与口径。
5. 墙钟成本：local 策略 +146~+538s/10k（争用注记后实际更大）；rss 两策略无差异
   （~900–1030MB，本次两侧都有采样）。

### 5.3 推荐默认（最终版，取代 §3 第 5 条的候选措辞）

- **全局发现**：random + local（稳健：147.45±10.26，方差最小）；上限取向 random-reuse +
  local（均值最高 258.90 但重尾，中位 194）。fps 保留为快路径（wall ≈0.55×，其下最优
  发现组是 pso-target）。
- **已接受区域加密**：random-reuse + fps（发现损失即加密语义本身，且 wall 最低 315s）。
- **定向补采样**：random + search target（不变；TR+local proximity 19.31 / 31.2%，
  discovery −4.13 不显著）。


## 6. PdCuNiP 迁移验证（2026-10-03 晚，sweep `20261002T005922Z` 收口后）

**先说清这个验证能回答什么**：PdCuNiP sweep 只有 fps 一个选择策略臂，因此 §5 的
"local vs fps 策略效应 20/20" **本身无法跨数据集直接检验**（缺 local×PdCuNiP 臂）。
可检验且预注册口径下干净的是：**优化器组效应（各组 vs 本 sweep 内 random 的逐 seed
配对差值）在两个材料间是否复现**——两侧各自同口径（scaled）、同材料、同策略，组内配
对完全合法。组内 PdCuNiP 的完整细节见
`docs/reviews/2026-10-03-pdcunip-sweep-analysis.md`（绝对均值/邻近度/工程事实）；
本节只做迁移判读。分析脚本 `tmp/analyze_pdcunip_migration.py`。

### 6.1 组效应迁移表（各组 − 本 sweep 内 random，unique_per_100_evals，20 seeds 配对）

| group | PdCuNiP + fps | carbon + local | 方向 | 显著性 |
|---|---|---|---|---|
| random-reuse | **+27.75±21.21 (18/20)**** | **+111.44±88.21 (20/20)**** | ✅ 一致 | ✅ 一致（幅度缩水 ~4×） |
| genetic | **+11.56±16.85 (17/20)**** | +7.45±37.99 (8/20 n.s.) | ✅ 一致 | ⚠️ 翻转（PdCuNiP 上转显著正） |
| genetic-target | **−30.03±5.77 (0/20)**** | **−12.38±12.36 (4/20)**** | ✅ 一致 | ✅ 一致（为负） |
| pso | −3.05±31.33 (7/20 n.s.) | **−41.74±58.72 (4/20)**** | ✅ 一致 | ⚠️ 翻转（carbon 上显著负） |
| pso-target | **−19.03±20.91 (2/20)**** | **−29.28±32.17 (3/20)**** | ✅ 一致 | ✅ 一致（为负） |
| target_region | **−30.40±5.58 (0/20)**** | −4.13±12.23 (7/20 n.s.) | ✅ 一致 | ⚠️ 翻转（PdCuNiP 上显著负） |

(random 基线：PdCuNiP 130.56±3.75 / carbon+local 147.45±10.26；全部 140 行
stopped_by=max_evaluations，无早停混杂。)

**判读**：方向 6/6 迁移；显著性 4/6 迁移。量级强依赖材料——reuse 的复利效应在
PdCuNiP 上只有碳的约四分之一；定向组的发现代价在 PdCuNiP 上显著放大
（target_region −4(n.s.) → −30(0/20)）。

### 6.2 覆盖半径的反转（次级指标，各组 − random）

PdCuNiP 上几乎全部组的覆盖半径显著**劣于** random（reuse −84.77、genetic
−167.09、pso −58.92、pso-target −55.04 均 0/20–4/20），**唯独 target_region
+9.43 (15/20)****——定向加密在多组分玻璃上用发现换来了真实覆盖增益，这笔交换在
carbon+local 上不存在（TR 覆盖 +10.70 20/20 但发现只 −4 n.s.）。genetic 在
PdCuNiP 上呈"发现↑覆盖↑↑（240 vs 407）"双优（详见并行文档结论 1）。

### 6.3 跨 sweep 原始配对差值（carbon_local − pdcunip_fps）——**混杂，仅存档**

reuse +100.59 (19/20)、genetic-target +34.54 (20/20)、target_region +43.17 (20/20)、
pso-target +6.65 (n.s.)、pso −21.80 (n.s.)、genetic +12.79 (n.s.)。
**该差值混杂了数据集与选择策略两个因子（数据集效应 ⊕ 策略效应），禁止作为策略效应
引用**——与 §5 解除的旧口径混杂同类，此处数字只作为未来 local×PdCuNiP 臂的对照基线。

### 6.4 结论（对 §5 推荐默认的约束）

- §5 的推荐默认表（全局发现=random+local / 加密=random-reuse+local / 定向=random+target）
  **保持 carbon 校准范围**：组效应方向全部复现，但幅度与显著性随材料改变，
  "无普适优化器默认"（并行文档结论 4）成立——新材料入库应跑同设计预注册 sweep。
- reuse 的方向稳健性（两材料两策略下从未显著为负）是六组中最稳的信号；
  pso-target 的定向失败（proximity 82 vs random 55）在两材料两策略下均复现，
  该组合可从 UI 推荐文案中进一步降级。
- 若未来需要检验策略效应本身在 PdCuNiP 上的大小，需补 local×PdCuNiP 臂
  （~19h wall，模板同本 sweep）。
