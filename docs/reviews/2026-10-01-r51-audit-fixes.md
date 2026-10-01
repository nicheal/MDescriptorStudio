# R5.1 审计修复记录(2026-10-01)

输入:外部审计报告(基于 main @ `8ed4bdb`,覆盖 `34c8835` R5.1 主体与 `8cf9eee` DPA4C 默认模型修正),随附回归测试文件 `tests/test_r51_audit_regressions.py`。审计结论经逐项源码核对**全部属实**;本批次落地其建议的 P1 修复与低风险 P2 修复。

## 与审计结论的核对结果

| 审计项 | 核对 |
|---|---|
| P1-1 writer 固定写 `energy_screened=false`,无测量值 | 属实(writer.py:173,注释还声称"本管线无筛选");candidates.jsonl 同样缺失 |
| P1-2 发现指标按筛选后选择集计数 | 属实(engine.py:724/818);engine.py:228 自身注释写明 "still counted as discovered",代码与文档自相矛盾;全拒绝轮两个指标为 0,可伪触发 discovery_saturated |
| P1-3 PSO observe 不检查 screening_rejection | 属实(pso.py:251);失败候选可进入 position/pbest |
| P1-4 快照不序列化 energy/energy_per_atom/max_force | 属实(engine.py:986 与恢复路径 547) |
| P2-5 capability 门只查模块存在;requirements 下限 0.3.4 | 属实 |
| P2-6 checkpoint 类型不校验、空白串不 trim | 属实(前端有 trim,后端无) |
| NaN 适配层防御缺口(官方 PredictionResult 已挡) | 属实,按"纵深防御"处理 |
| 缓存键无模型内容 digest | 属实(含 checkpoint 路径、不含内容) |

## 改动清单

### 1. P1-1 — writer 动态状态与测量值(`artifacts/writer.py`)

- 五字段状态模型逐帧计算:`energy_screened` / `energy_screen_pass` = 该帧是否携带筛选测量值(fail 永不进入 accepted;unscreenable 无测量值);`train_set_ready` = 携带测量值**且**请求配置了至少一个上限——纯测量 pass(两上限均未设)不作可训练宣称(审计"仍需澄清"项在此一并收口,判据读 `request.constraints.energy_screening`)。
- 测量值入帧头:`screen_energy` / `screen_energy_per_atom` / `screen_max_force`(加 `screen_` 前缀,避免与 extxyz 标准能量键混淆、防止被误读为 DFT 参考值)。
- accepted 但 unscreenable 的帧写 `energy_screen_status="unscreenable"`,解释测量值缺失原因。
- candidates.jsonl 增补 `screening_status` / `screening_reasons` / `energy` / `energy_per_atom` / `max_force`。

### 2. P1-2 — 发现计数回到筛选前口径(`engine.py`)

- 选择后即固化 `discovered = list(selected)`(筛选前);`unique_novel_environments` 与 raw `novel_environments` 均改按 `discovered` 计数——落实轮记录 docstring 的既有契约("still counted as discovered"),全拒绝轮不再伪触发 discovery_saturated。
- 新增轮字段 `archived_unique_novel_environments`:按筛选后保留集的严格去重计数(无拒绝时等于发现值,全拒绝时为 0),入 `to_json`。
- **停机口径**:discovery_saturated 继续读"发现"口径——筛选把物理不合理结构挡在档案外,但不能"取消发现"其探索到的描述符空间区域;严格筛选不得伪造饱和(注释已写明)。
- 无筛选运行行为逐位不变(`discovered == selected`,新字段恒等于发现值)。

### 3. P1-3 — PSO 记忆排除筛选失败(`optimizers/pso.py`)

- `observe` 跳过 `screening_rejection` 非空的观测:**仅排除明确的筛选失败**,不排除全部 `accepted=false`——未被 FPS 选中的有效候选仍是合法记忆目标(审计特别警告点)。

### 4. P1-4 — 快照保留测量值(`engine.py`)

- `snapshot_state` 的 evaluations 序列化增补 energy / energy_per_atom / max_force / screening_status / screening_reasons;恢复路径回填(`.get()` 宽容旧快照)。

### 5. verdict 溯源(`models.py` + `engine.py`)

- `CandidateEvaluation` 增补 `screening_status` / `screening_reasons`;引擎把保留候选的完整 verdict(含 unscreenable)写入评估记录——"可归档但原因丢失"收口。轮记录不新增 unscreenable 计数字段:该信息经评估记录与 jsonl 已可追溯,轮级聚合留待有前端消费方时再加。

### 6. P2 — capability 门 / requirements / checkpoint 校验 / NaN 防御(`screening.py` 等)

- `mdescriptor_predictors_available()`:从 `find_spec` 探测改为实际 `from mdescriptor.predictors import DPA4C, NEP` + `hasattr(mdescriptor._native, "Dpa4cPredictor")`——残缺 wheel 在提交时被拒,而非排队后在 worker 失败。仍未做模型加载/校验和/CUDA 探测(见"明确不做")。
- `requirements.txt` 下限 `mdescriptor>=0.3.4` → `>=0.3.5`(0.3.5 起才有 predictors 包)。
- `ScreeningSpec.__post_init__`:checkpoint 非字符串拒绝;字符串 trim 后为空视为 None(默认内置模型)——对齐前端 trim 行为。`EnergyForceScreen` 内 `or None` 相应移除。
- 适配层 NaN 防御:预测的 total/per_atom/max_force 任一非有限 → `unscreenable("non_finite_prediction")`(官方 0.3.5 `PredictionResult` 上游已拒非有限值,此为第三方替身的纵深防御)。predictor **报错**仍整轮传播(全局故障 ≠ 单帧不可判),screening 模块 docstring 已改为与实现一致的表述。
- 缓存键:启用筛选的运行折叠 `importlib.metadata.version("mdescriptor")`——内置模型随 wheel 升级后不再复用旧筛选结果;未启用筛选的运行 cache key 不变,存量缓存不受影响。
- 前端 `types.ts`:`GenerationRound` 补 `rejected_screening?` 与 `archived_unique_novel_environments?`(当前 UI 无轮次表格组件,仅补类型)。

## 明确不做(与审计口径对齐)

- **提交时完整能力预检**(加载/校验和内置模型、显式 checkpoint 可加载性、CUDA 探测):本机开发环境为 0.3.4(升级刻意暂缓,见 34c8835 提交说明),无法对真 0.3.5 验证此类深检;且提交时加载模型是独立的延迟取舍。native 门已覆盖审计的最小回归案例,深检留待 0.3.5 环境可用时补。
- **显式 checkpoint 内容 digest 进缓存键**:同路径换文件的漂移未覆盖(docstring 与缓存键注释均记为已知限制);内置模型漂移已由 wheel 版本折叠覆盖。
- **单帧"predictor 异常 → unscreenable"**:审计自己的通过用例把"异常传播"钉为正确行为(配置错误在提交时拦截);仅按三态契约补了非有限值路径。
- **NEP/DPA4C 共用 checkpoint 字段的错误模型文件/切换行为补测**:需真 wheel。
- **逐原子力标签存储**:超出 R5.1 范围(审计列为未来关切)。

## 测试

`tests/test_r51_audit_regressions.py`(审计文件入库,7 个 xfail 标记随修复逐条移除;真 wheel 用例加 `importorskip("mdescriptor.predictors")` 守卫,0.3.4 环境跳过):

- 修复行为 7 例:NaN → unscreenable;writer 测量值 + 动态标志;PSO 拒绝失败候选进记忆;全拒绝轮发现指标 > 0 且档案为空;快照往返保留 energy;真 wheel 默认/显式 checkpoint 解析一致(NEP/DPA4C 参数化);native 门。
- 审计确认正确的行为 7 例:混合周期 verdict 对齐、非法 checkpoint 类型、官方 PredictionResult 有限性契约、旧 wheel 拦截、精确等于阈值通过 + predictor 异常传播、(参数化)真模型解析。

## 验证

- **红-绿**:修复前 HEAD 上 6 例严格 xfail 如期失败(xfail 复现)、3 过 4 跳;修复后(移除标记)9 过 4 跳,零失败。
- **全量 backend**:`683 passed, 5 skipped, 0 failed`。记忆中 2026-09-25 的 5 个既有环境失败(hdbscan ×2、descriptor_flow ×2、backend_smoke)本次未复现,基线为 0 失败。golden 随机基线通过——无筛选路径行为逐位不变的直接证据。
- **前端**:vitest 251 passed;`tsc -b` 干净。
- 修复过程中发现并纠正一处自引入缺陷:归档循环最初引用了 gate 循环遗留的 `verdict` 变量(无 screening 时潜在 NameError、有 screening 时测量值错位),由快照往返测试当场暴露,已改为按 index 从 `screening_verdicts` 取值。

## 提交范围

backend(screening/models/engine/pso/writer/generation_service/requirements)、tests/test_r51_audit_regressions.py(新)、frontend types.ts、docs(矩阵更新 + 本记录,`git add -f docs/`)。工作区中与本批次无关的既有改动(`frontend/src/global.css`、`tests/data/LiICOF.xyz`、`tests/data/PdCuNiP.xyz`)不动。
