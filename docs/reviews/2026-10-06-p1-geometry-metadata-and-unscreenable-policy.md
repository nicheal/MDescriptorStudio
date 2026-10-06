# P1 批次:生效几何约束落盘 + unscreenable_policy + coverage 术语(2026-10-06)

P1 清单七项中的 #1/#2/#5(评审原文已丢失,意图从代码重建;#3/#4 为功能设计,
见文末决策点)。同批顺手修复 main 上预先存在的 `tsc -b` 断裂(zh.ts 重复键)。

## #1 生效几何约束落盘(→ 几何重算自含)

- 缺口:`metadata.json` 的 `request` 回显携带的是**校验后但未补全默认值**的
  constraints dict(如 `min_distance_mode` 缺省时不写回)——仅凭产物无法
  独立重算几何判定。
- 实现:`generation_service._geometry_constraints_metadata(constraints,
  request_constraints)`(模块级、可直测),把 `build_constraints` 的生效
  dataclass 渲染为 JSON dict(默认值填齐:`min_distance_mode="none"`、
  `min_distance_factor=0.7`、双锁 True;pair-cutoff 矩阵还原为规范化
  pair 表),经 `metadata_extra` 写入 `metadata.json` 的
  `geometry_constraints` 键。原始 request 回显保持不动。
- 测试:defaults 补全 + 显式值逐字携带(gates 文件新类,2 项)。

## #2 unscreenable_policy(不可判定帧的策略)

- 缺口:unscreenable 判定(部分周期性、非有限预测)一律保留入库,
  无用户选择权;"无法判定"与"判定合格"在归档流里不可区分。
- 实现:`ScreeningSpec.unscreenable_policy: "keep"(默认)| "reject"`,
  `validate()`/`__post_init__` 契约;`from_constraints` 读取;引擎筛选门:
  reject 时 unscreenable 判定与 fail 同路(不入库、不反馈、记录 reasons),
  **发现指标不变**(两侧都数 pre-screening selection),fail 永远不可保留;
  RoundRecord.rejected_screening 文档更新(fail 数 + reject 策略下的
  unscreenable 数)。
- 解析与缓存键:models 的 energy_screening 白名单加键、enabled 门内校验;
  **缺省时不写回**(键在 constraints dict 内参与 cache_key,写回默认值会
  无行为理由地失效所有既有 screening 缓存)——spec 指纹用 asdict 自动
  覆盖新字段,显式 reject 的缓存键自然不同。
- 前端:types/store 默认 "keep";submission 仅在 reject 时发送;卡片
  (ENERGY AND FORCE SCREENING)新增 Select(保留待审 / 如同拒绝丢弃)+
  说明行;zh 翻译齐;submission.test 2 项钉映射。
- 测试:引擎门 keep/reject 各一项 + spec 校验 + from_constraints + 解析
  3 项(screening 文件新 8 项)。

## #5 coverage 术语统一

- 调查结论:UI 侧在 R5.3 时代已统一(objective = Coverage gain/覆盖度增益,
  指标 = Coverage radius/覆盖半径,图 Coverage radius per round)。
- 残余动作:验证矩阵新增 **Coverage 术语表**——载荷键 `coverage` 保持历史
  名(改名破坏 params_json/缓存键),引用口径必须写明 gain 还是 radius;
  analysis 域 coverage 另属一套语义。

## 同批修复:main 上 zh.ts 重复键(tsc -b 断裂)

4784007(Statistical V2)在 zh.ts 同时留下 `"Points"` 两个条目
(轨迹页"散点" vs 诊断"点数"),`npx tsc -b` 自该提交起在 main 上报
TS1117。修复:analysisVisualizations 三处(统计行 + 两个直方图轴)改用
独立键 `"Number of points"`(点数),轨迹页保留 `"Points"`(散点)。

## 验证

- 后端 **836 passed + 1 skipped + 0 failed**(上一批 828 + 新 8:metadata
  2 + screening 引擎/策略 6)。
- 前端 `npx tsc -b` 干净(含上述修复)、vitest **254/254**、e2e **64/64**。
- 黄金基线不受影响(objective/解析层改动不触引擎轨迹;screening 默认
  keep 路径行为逐位不变)。

## 决策点(#3/#4,未实现——需要用户定义)

- **#3 relative energy window**:相对能量窗需要"参考分布"的来源决策——
  (a) 数据集 extxyz 自带的 DFT 能量 vs (b) 预测器重算参考集;两者存在
  系统性偏移不可混用;(b) 需在 submit/worker 期重算参考集(6738 帧量级,
  成本可观,或抽样)。窗形(±kσ / 分位窗)也需定义。
- **#4 cost-aware selection**:"代价"的定义未定(原子数≈描述符计算成本的
  代理?)以及它在选择里的语义(同增益偏好低成本 vs 预算按代价折算)——
  改变选择语义,需要明确目标后再动。

## P1 清单状态

#1 ✅ #2 ✅ #5 ✅(本批);#3/#4 决策点待定义;另:objective schema 提交期
验证 + CoverageGainObjective 吞参修复已在本日前批完成
(2026-10-06-objective-schema-validation.md)。
