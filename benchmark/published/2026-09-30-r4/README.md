# R4 预注册重跑 · 发表级汇总包(P1-06)

- 场景:`config.frozen.json`(= 仓库 `benchmark/config.json`,注册于任何重跑之前)
- 逐 seed 数据:`run_results.jsonl`(140 行,每行一个 run 的完整记录,含逐轮收敛数据)
- 汇总:`summary.json`;环境:`environment.json`(Python 3.12.9 / 32 核 / numpy 1.26.4 / scipy 1.15.3 / mdescriptor 0.2.3)
- 完整性:`SHA256SUMS` 对本目录全部文件

## 可复现内容

- **配对分析**(主指标 unique_per_100_evals 的逐 seed 配对差、bootstrap 95% CI、胜率、proximity 汇总):仅需本包即可完整重算——`docs/reviews/2026-09-30-r4-preregistered-sweep.md` 的全部表格由 `run_results.jsonl` 生成。
- **描述符重跑**:需要 carbon 数据集(`ds_d56748fb4391`,6738 帧 extxyz)与 NEP 描述符运行 `run_57a8b8c40286` 的模型/参数——数据未随包公开,取得说明联系仓库作者;`SHA256SUMS` 中数据指纹见 `docs/generation_verification_matrix.md` §3。

## 生成方式

`python benchmark/genetic_vs_random.py --config benchmark/config.json`(2026-09-29 启动,经历一次夜间中断后由 `benchmark/resume_sweep.py` 从检查点补齐;harness 版本 2026-09-29,见验证矩阵的版本字段)。
