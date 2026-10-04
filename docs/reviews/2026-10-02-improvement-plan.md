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

- **C — 产品化 R5.5 resume(P1,最大单项)— ✅ 已完成(2026-10-02,同日批次)**
  - 引擎快照 **v3**:evaluated 候选记录(描述符空间地图数据)作为第三个带哈希数据文件入快照——resumed 运行重发布的地图与 `generation.pca` 计数完整;v2 快照加载即拒(无生产快照,兼容负担为零);
  - 快照持久化:worker `on_round` 在 preview 更新后把引擎快照写入 `data_dir/generation_snapshots/<id>/`(事务性写 + 成功后才推进 DB 行——写失败保留上一个一致边界,不致中断健康运行);运行结束(COMPLETED/CANCELLED)清理快照并置 `resumable=0`;
  - DB migration 15:`generation_runs` 增 `snapshot_version`/`snapshot_path`/`resumable`/`last_snapshot_generation`;`_LIST_COLUMNS` 同步;
  - 状态语义:新增 `INTERRUPTED`;后端重启/关闭的僵尸清扫把"RUNNING 且有快照"的运行改标 INTERRUPTED(`resumable=1`),无快照 RUNNING 与 QUEUED 仍 CANCELLED;`_settle_linked_runs` 对 FAILED/CANCELLED 运行显式清 `resumable`;
  - 新 RPC `generation.resume`:状态守卫(仅 INTERRUPTED+resumable,WHERE 守卫防并发重复恢复)、快照版本校验(≠当前 SNAPSHOT_VERSION 拒绝)、`params_json` 补回 dataset/descriptor id 后重解析为类型化请求、新鲜度/种子视图门复用;worker 复用 `_run_generation(resume_snapshot=...)`:`restore_state()` 事务校验全部快照(指纹含 B 的身份契约——dataset 重导入/描述符重指/checkpoint 换字节/约束改动都拒绝),preview 累计器从快照轮次历史播种;
  - 产物连续性:accepted.extxyz/candidates.jsonl/npy 数组经恢复的 accepted+evaluations 完整重发布;evaluated 几何 spool 只覆盖恢复后部分 → manifest 记录 `evaluated_structures.offset`,`generation.structure` 对偏移前的 accepted 点经 accepted.extxyz 兜底解析(其余前段点报"几何未持久化",不猜);
  - 前端:INTERRUPTED 徽章(purple)+ Resume 按钮 + 中断说明 Alert;轮询对 INTERRUPTED 停止;mock 后端补 `generation.resume`;zh 翻译;
  - 验收:`tests/test_generation_ipc.py::test_generation_resume_after_interruption`——固定种子跑→等首个轮次快照→**硬杀后端进程**→同数据目录重启→INTERRUPTED+resumable→resume 完成 6 轮→与独立数据目录中的不间断参照运行**轮次记录逐字节一致**(rounds JSON/accepted/evaluations),地图完整、offset 兜底可用;另有僵尸清扫语义测试、resume RPC 五项守卫测试、快照 v3 roundtrip 全绿。
- **D — screening UI 语义补全(P1)— ✅ 已完成(2026-10-02,提交 `0405ef6`)**
  - 引擎 RoundRecord 增筛选三态分解:`screening_passed` / `screening_unscreenable`(fail = `rejected_screening`)与 `screening_train_ready`(= 配置了至少一个能量/力上限的 pass——writer 的 train_set_ready 语义在源头唯一定义);字段默认 0、restore 宽松,旧 v3 快照可继续加载;
  - 终态 preview 携带 `screening_model_identity`(解析来源 + sha256 + 包版本)——结果页展示"加载了什么",而非仅配置路径;
  - 前端:"Other candidates not selected" 不再吸收筛掉的候选——能量/力拒绝单独成行,与通过/无法筛选/可训练计数并列;筛选激活时"可训练"进主指标行;收敛图在归档(筛选后)发现曲线与发现曲线出现分歧时叠加第二序列(虚线);详情面板显示阈值与模型身份;
  - i18n 完整性测试拦截了缺失的 `force` 翻译——补齐;
  - **global.css 死类审计完成(计划遗留事项闭环)**:20 个未被源码引用的选择器全部是 Ant Design / Plotly 库内部类覆盖(`ant-*`、`js-plotly-plot` 等),属有意样式,未删除;早前记录的"死类"(`.generation-config-grid/.generation-config-root`)已被组件实际使用;
  - 验证:后端 747 通过(5 个已知环境失败)、前端 tsc/eslint/252 单测、E2E 58/58。
- **E — 契约澄清 + gen-5 科学指标(P2;须等在跑 sweep 结束)**
  - ~~锚点语义落地~~ **✅ 提前完成(2026-10-02)**:种子范围汇总行显示"视图 + N 个锚点"(`{count} anchor(s)` 插值键,zh 翻译齐备);锚点清单本就随 params_json 持久化并在请求回显中可见——决策 2 的两处小改全部落地,且为纯 UI 汇总、非行为变更,不受 sweep 门控;**剩余 E 严格只剩 gen-5 版本提升相关项(E2 版本提升与指标、E3 GA/PSO 局部目标、E4 SSW 矩阵注记已随 091645c 完成)**;
  - `GENERATION_ALGORITHM_VERSION` 升 gen-5:新增排列不变 `strict_unique_v2` 计数(与 gen-4 口径并存,不动已发布 R4 数值)、`screening_bottleneck` 终止原因(读 archived 发现率,与 descriptor 饱和区分)、archived 发现率次级指标;
  - 把原子空间目标距离暴露到 `OptimizationContext`,补齐 GA/PSO 局部目标搜索(G5 收尾;SSW 按决策 3 移出);
  - 同步更新验证矩阵版本字段与口径注解。
- **F — 冻结后再基准**
  - gen-5 冻结 → 新预注册(碳 + PdCuNiP,同 repeats/预算/目标/阈值/算子族/主指标)→ 报告 pre-screen 与 archived 两套发现率 → 发布包附数据集获取方式,关闭 L5 独立复现行。

## 5. 遗留事项

- 本机 5 个既有环境失败随 mdescriptor 0.3.5 升级一并消除(升级本身是挂起事项,须等 sweep 结束、env 隔离进行);
- `tests/data/LiICOF.xyz`(13.3MB)/`tests/data/PdCuNiP.xyz`(64.3MB)保持不入库(2026-10-01 审查结论);
- ~~`frontend/src/global.css` 的死类清理~~ **已闭环(2026-10-02)**:审计结论为无可删项——全部未引用选择器均为库内部类覆盖,见 §4 D 记录。

## 6. 完成状态(2026-10-02 晚)

| 里程碑 | 状态 | 证据 |
|---|---|---|
| A — CI 转绿 + 数据完整性 | ✅ | `f526a61`,CI run 37007251566 success |
| B — 模型/描述符身份 | ✅ | 同上(与 A 同提交) |
| C — 产品化 resume | ✅ | `57cb04a`,CI run 37015303356 success;杀进程→重启→resume 字节级一致验收 |
| D — screening UI 语义 | ✅ | `0405ef6`(CI 经后继提交链确认 success);后端 747/前端 252/E2E 58 |
| E1 — 锚点语义落地 | ✅(提前,不受 sweep 门控) | `90a6ea4`:种子范围汇总行 "+ N 个锚点";锚点清单本就随 params_json 持久化 |
| E2–E4 — gen-5 指标/GA-PSO 局部目标 | ✅(2026-10-03,sweep 收口后) | `8d10b3b`:strict_unique_v2 排列不变计数(两种排列回归钉住)、screening_bottleneck 停机、GA/PSO 局部目标核+解析门放开、GA 快照距离表;后端 818/0、前端 252;tsc 的 zh.ts 重复键报错来自并行在途批次,本提交只暂存 generation 侧 hunk |
| F — 冻结后再基准 | 🔄 进行中(2026-10-04 启动) | 预注册 `config.gen5-carbon.json`/`config.gen5-pdcunip.json`(v2 主指标+双口径次级,harness 升 2026-10-03);两 sweep 并行运行中(记录 `docs/reviews/2026-10-04-f-gen5-preregistrations.md`);分析+发布包+L5 收口等完成 |
