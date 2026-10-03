# PdCuNiP preregistered sweep — within-sweep analysis record (2026-10-03)

Sweep: `benchmark/results/20261002T005922Z/` (140/140, 2026-10-02 08:58 → 10-03 ~20:50,
ran concurrently with the fps re-baseline for its first ~2.5h; annotation applies to
early rows' wall only). Config `benchmark/config.pdcunip.json`: R4 frozen scenario,
`structure_fps_v1`, metric_caliber strict-unique-scaled, dataset `ds_9ca89d14f8f0` /
run `run_644f6186340c`, anchors **2256,2133**, 20 repeats × 7 groups × 10k evals, all
rows stopped by `max_evaluations`. Analysis script `tmp/analyze_pdcunip.py`.

Per the prereg contract: primary comparison = paired per-seed unique_per_100_evals
WITHIN this material; anchor proximity interpreted for targeting groups only; coverage
radius smallest-is-best; **cross-material value comparisons against carbon are out of
scope** (ranking transfer is assessed qualitatively).

## A. 绝对均值（fps 策略，scaled 口径）

| group | unique/100 | coverage (小优) | accepted | wall s | rss MB | prox median | within_r |
|---|---|---|---|---|---|---|---|
| random | 130.56±3.75 | 407.28±6.50 | 170.6 | 981.3 | 1219 | 54.77 | 2.4% |
| random-reuse | **158.31±19.73** | 322.50±40.24 | 218.8 | 1014.2 | 1210 | 64.78 | 0.5% |
| genetic | 142.12±18.06 | **240.19±32.26** | 178.4 | 1025.5 | 1228 | 153.56 | 2.0% |
| genetic-target | 100.53±3.72 | 386.06±14.85 | 180.0 | 877.3 | 1178 | 37.33 | 5.2% |
| pso | 127.51±32.48 | 348.36±69.98 | 200.7 | 900.6 | 1188 | 63.25 | 0.9% |
| pso-target | 111.53±20.62 | 352.23±45.50 | 187.3 | 865.0 | 1182 | 74.33 | 5.1% |
| target_region | 100.16±3.34 | 416.71±9.92 | 185.4 | 775.4 | 1176 | **33.04** | 4.3% |

## B. 组内配对 vs random（预注册主指标）

| group | unique/100 差值 (wins) | coverage 差值 (wins=WORSE-cov) |
|---|---|---|
| random-reuse | **+27.75±21.21 (18/20)** | −84.77 (0/20 worse) |
| genetic | **+11.56±16.85 (17/20)** | **−167.09 (0/20 worse = 20/20 更优覆盖)** |
| pso | −3.05±31.33 (7/20, n.s.) | −58.92 (4/20 worse) |
| pso-target | −19.03±20.91 (2/20) | −55.04 (3/20 worse) |
| genetic-target | −30.03±5.77 (0/20) | −21.22 (2/20 worse) |
| target_region | −30.40±5.58 (0/20) | +9.43 (15/20 worse) |

## C. 定向组锚点距离

target_region 33.04（min 31.28 / max 36.30，稳定）与 genetic-target 37.33 均显著优于
random 的 54.77；**pso-target 74.33（min 41.49 / max 155.86，组间剧烈波动）——在两个材料上
都无法定向**，与 carbon 结论一致。

## 结论（R4 default-switch gate 的答案：**不迁移**）

1. **优化器排名随材料改变**（同为 fps+scaled）：carbon 上 reuse 是毒药（24.45，垫底），
   PdCuNiP 上它是最优发现组（158.31，+27.75 18/20）；carbon 上 genetic 接近垫底
   （59.16），PdCuNiP 上它第二（+11.56 17/20）且**覆盖半径大幅最优**（240 vs random 407，
   20/20）——GA 在多组分材料上有真实的加密/覆盖生态位。
2. **定向机制迁移、发现代价不迁移**：TR/genetic-target 的 proximity 优势在两材料都成立
   （33/37 vs 55），但 discovery 代价从 carbon 的 −4/−12 扩大到 PdCuNiP 的 **−30（0/20）**；
   pso-target 两材料都定向失败。
3. **跨材料数值比较确实不可做**（预注册的判断被证实）：random 的 unique/100 在两材料相差
   近一倍（70.0 vs 130.6）——材料描述符空间本身不同。
4. **没有普适的优化器默认值**：目标导向默认值表（全局发现/加密/定向）是 carbon 校准的；
   新材料入库即应跑同设计预注册 sweep（本记录即第二材料的模板）。random 仍是最稳基线
   （±3.75，两材料方差都最小）。
5. 工程事实：PdCuNiP（9615 帧、736798 原子行）wall 775–1025s/10k、rss ~1.2GB，均可承受。

## 工作流位置

两 sweep（fps 重跑基线 + PdCuNiP）均已收口 → **E2–E4（gen-5）门控解除**，F（重跑基准 +
L5 数据发布）就绪。本记录为 R4 计划的收官文档之一。
