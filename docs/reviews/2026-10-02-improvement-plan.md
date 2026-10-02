# 2026-10-02 外部审查改进计划与执行记录

- 审查对象:`main` @ `f3c6dd8`(外部审查报告,生成引擎/优化器/screening/工件/resume/前端/基准全量)
- 本文档:审查论断的核实结论、本轮(里程碑 A+B)执行记录、C–F 路线图与两项用户决策
- 执行提交:`f526a61`(A+B 代码与测试);docs 随本文件所在提交

## 1. 审查论断核实(三个并行代码勘察,全部逐条比对)

| 审查论断 | 核实结论 | 证据 |
|---|---|---|
| P0 候选 ID 碰撞(strain 量化 ID) | 属实 | `operators/strain.py:46` `f"{parent}_s{int(\|ΔV\|·1e6)}"`,iso/aniso strain 与 shear 共用;`abs()` 还把压缩/拉伸折叠;GA/PSO 以对象身份绕开(`genetic.py:127` 注释自证);引擎运行时**无**重复守卫,快照注册静默丢弃碰撞者,restore 只查已写入文件 |
| P0 指纹缺几何约束 | 属实 | `engine.py` `_config_fingerprint()` 未含 `GeometryConstraints` 全部 10 字段(`constraints/geometry.py:90-103`);resume 测试对约束变更 0 覆盖 |
| P0 CI 后端红灯 | 属实 | `screening.py:32-47` 只做真导入+native 检查(R5.1 加深),测试的 `find_spec` monkeypatch 失效;CI 环境装了完整 wheel 故断言失败 |
| P0 CI 前端红灯 | 属实,且修复已在工作区 | `generation-interactions.spec.ts:256` 的 1920px 断言;未提交的 `global.css` 容器查询 grid 规则即修复 |
| P1 checkpoint 内容未哈希 | 属实 | `_cache_key` 含路径+包版本,注释自认"replaced in place is not captured";`resolved_model.digest` 加载时已算但从未持久化 |
| P1 evaluator 身份仅类名 | 属实 | 指纹 `"evaluator": type(self.evaluator).__name__` |
| P1 resume 未产品化 | 属实 | `write_snapshot`/`restore_state` 全仓库仅 engine.py+测试调用;`on_round` 只更新 preview_json;worker 在 `TemporaryDirectory` 中;无 INTERRUPTED 状态,重启僵尸清扫直接改 CANCELLED |
| P1 screening 计数 UI 未呈现 | 属实 | `summary.ts otherCandidateCount` 忽略 `rejected_screening`;该字段与 `archived_unique_novel_environments` 前端零渲染 |
| P2 锚点绕过 seed_view | 属实但有减轻情节 | 服务端确实强制前置(`generation_service.py:564-576`),但前端提示文案**已披露**("Anchors can sit outside the selected seed view");仅种子范围汇总行未体现 |
| P2 严格计数顺序依赖 | 属实(已文档化) | docstring 明示"非排列不变量",由测试钉住;审查亦认可非隐藏 bug |
| P2 饱和用预筛选发现数 | 属实(刻意设计) | `_discovery_saturated` 读 pre-screen 口径,注释言明"严格筛选不得伪造饱和";`archived_unique_novel_environments` 未接入停机 |

## 2. 本轮执行记录(里程碑 A+B,`f526a61`)

**里程碑 A — CI 转绿 + 数据完整性(P0,不改搜索行为)**

- A1 `mdescriptor_predictors_available()` 增加显式 `find_spec("mdescriptor.predictors")` 预检,保留真导入+native `hasattr` 深层检查(旧轮测试重新生效;native 缺失用例已存在,CI 环境运行)。
- A2 工作区 `global.css` 容器查询规则(`.generation-config-grid` + `@container (min-width:1080px)` 三列)验证后入库;`generation-interactions` wide/narrow E2E 本地通过。
- A3 候选 ID 防碰撞(不消耗 RNG、gen-4 不升版):
  - strain/shear ID 追加形变矩阵 blake2b 短摘要(`_s{ΔV}_{8hex}`),同 |ΔV| 不同形变在源头即不同 ID;
  - 引擎运行时不变量:`_issued_candidate_ids`(构造时以种子池初始化、拒绝重复种子 ID;restore 时从快照回填);碰撞提案**原位改名** `<id>_x<k>`(保持对象身份,GA/PSO 的 `id()` 键控父代记忆不受影响;改名优于丢弃——不浪费提案预算、不改变评估数量);`snapshot_state` 对 accepted 集合加硬断言;
  - golden Random 基线重镕:脚本验证 9 处差异**全部**只是 strain ID 后缀、positions/fitness/stopped_by/evaluations 逐一相同后重写。
- A4 `_config_fingerprint()` 以 `_constraints_fingerprint()` 并入约束全量(pair cutoff matrix 以 shape+sha256 表示);restore 错误信息同步。
- A5 resume 回归:五类约束变更(min_distance/pair_cutoff/composition_lock/volume_bounds/displacement_bound)快照后 restore 必须拒绝;另加 restore 回填已发放 ID 集、跨恢复边界碰撞改名两测试。
- A6 后端全量 738 通过;**5 个失败为与本批无关的既有环境失败**(hdbscan/analysis_ipc/backend_smoke/descriptor_flow×2,已在干净 HEAD 工作树复现——本机 mdescriptor 0.3.4 旧轮所致,CI 装新轮不受影响);前端 tsc/lint/251 单测/E2E 绿。

**里程碑 B — 统一模型/描述符身份(P1)**

- B1 `DescriptorEvaluator.signature()`:描述符名、规范化参数、adapter、device、mdescriptor 包版本;`EnergyForceScreen.model_identity()`:模型类型、checkpoint 路径、**解析资源的 source/path/sha256**(复用加载时校验的 digest,内置默认模型同样覆盖)、包版本。
- B2 四处接入:引擎指纹(evaluator signature 替换裸类名;screening 身份并入 `_screening_fingerprint`);提交缓存键(显式 checkpoint 以**文件内容 sha256** 入键——原地换模型不再复用旧缓存,不可读 checkpoint 提交即报错;内置默认仍由包版本覆盖);`metadata.json` 增 `screening_model_identity`;测试 stub 经 `getattr` 回退不受影响。
- B3 回归:同路径不同字节 → 缓存键不同;screening 身份/evaluator 签名变更 → 快照 restore 拒绝(相同则放行);signature 单元测试。

**实现说明**:计划原定 A、B 两次提交;实际 `screening.py`/`engine.py` 同时承载两批改动,拆分需在函数体内分 hunks 且会产生测试不绿的中游提交,故合为一次代码提交(`f526a61`),文档单独成提交。

## 3. 用户决策(2026-10-02)

1. **本轮范围 = A+B**;C–F 按下节路线图排期。
2. **锚点语义 = 保持增强**:锚点不在所选种子视图内时仍并入种子池(现状,前端提示已披露);后续落地时把种子范围汇总行改为"视图 + N 个锚点"并把锚点清单写入运行元数据/lineage(归入 E 前的契约澄清,不构成行为变更)。
3. **SSW/外部优化器 = 正式移出范围**:原 G5 规划中的 ExternalGenerationAdapter/SSW 不再保留;G5 以"局部目标搜索覆盖 GA/PSO"收尾(见 E3)。验证矩阵已同步标记。

## 4. 路线图(未排期,按序执行)

> 顺序约束:两个在跑 sweep(PdCuNiP `20261002T005922Z`,约 2–3 天;碳 fps 重基线 `20261002T010733Z`,约 1.5–2 天)期间,只允许"非行为变更"(不动 gen-4、不消耗 RNG、只影响引擎快照/缓存键)的修改——A/B 即属此类,benchmark resume 只校验 algorithm_version 字符串,不受影响。**E 的版本提升必须等两个 sweep 结束并归档后才能开始**;C/D 无此约束,可先行。

- **C — 产品化 R5.5 resume(P1,最大单项)**
  - 快照持久化:`on_round` 在 preview 更新后把引擎快照写入 `data_dir/generation/<id>/snapshot/`(限频),脱离 `TemporaryDirectory` 生命周期;
  - DB migration:`generation_runs` 增 `snapshot_version`/`snapshot_path`/`resumable`/`last_snapshot_generation`;
  - 状态语义:新增 `INTERRUPTED`;后端重启僵尸清扫把 RUNNING 改标 INTERRUPTED(可恢复)而非 CANCELLED;用户取消仍为 CANCELLED(不承诺恢复语义);
  - 新 RPC `generation.resume`:按运行参数重建描述符/档案/算子/screening 上下文(依赖 B 的身份契约校验 checkpoint/描述符未变)→ `restore_state()` → 续跑;产物连续性:从快照重放 accepted 候选,重新生成完整 `accepted.extxyz`/`candidates.jsonl`;
  - 前端:运行页对 INTERRUPTED 运行提供 Resume 操作与状态徽章;
  - 验收:固定种子跑 2 轮 → 进程终止/重启 → resume → 与不间断运行逐项一致(字节级)。
- **D — screening UI 语义补全(P1)**
  - `otherCandidateCount()` 计入 `rejected_screening`,"Other candidates not selected" 拆出"能量/力筛选拒绝";
  - 轮次图表并列 `unique_novel_environments` 与 `archived_unique_novel_environments`(切换或双线);
  - 后端 RoundRecord 增轮级 pass/fail/unscreenable 计数(非行为变更)并贯通 preview;突出 train-ready 计数;unscreenable 不得与 pass 同权呈现;
  - 运行详情展示 screening 模型身份(B 的产物)与阈值。
- **E — 契约澄清 + gen-5 科学指标(P2;须等在跑 sweep 结束)**
  - 锚点语义落地(决策 2 的两处小改);
  - `GENERATION_ALGORITHM_VERSION` 升 gen-5:新增排列不变 `strict_unique_v2` 计数(与 gen-4 口径并存,不动已发布 R4 数值)、`screening_bottleneck` 终止原因(读 archived 发现率,与 descriptor 饱和区分)、archived 发现率次级指标;
  - 把原子空间目标距离暴露到 `OptimizationContext`,补齐 GA/PSO 局部目标搜索(G5 收尾;SSW 按决策 3 移出);
  - 同步更新验证矩阵版本字段与口径注解。
- **F — 冻结后再基准**
  - gen-5 冻结 → 新预注册(碳 + PdCuNiP,同 repeats/预算/目标/阈值/算子族/主指标)→ 报告 pre-screen 与 archived 两套发现率 → 发布包附数据集获取方式,关闭 L5 独立复现行。

## 5. 遗留事项

- 本机 5 个既有环境失败随 mdescriptor 0.3.5 升级一并消除(升级本身是挂起事项,须等 sweep 结束、env 隔离进行);
- `tests/data/LiICOF.xyz`(13.3MB)/`tests/data/PdCuNiP.xyz`(64.3MB)保持不入库(2026-10-01 审查结论);
- `frontend/src/global.css` 的死类清理(早前挂起事项)仍未做,与 C/D 前端改动一并处理即可。
