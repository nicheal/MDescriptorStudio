# 2026-09-26 — Search Target 重构:目标区域从优化器方法升格为独立搜索轴

背景:`2026-09-25-g5-2-target-region.md` 把定向补采样实现为第四个优化器
方法;评审时提出"目标区域到底是优化器方法还是搜索目标?"——实现上落在
优化器槽位,概念上它是一份**用户输入**(在哪找),与"怎么提案"(方法)
正交。耦合的实际代价:无法组合(random+区域、GA+区域都表达不了)。
本重构按用户决策把两者拆开。

## 新结构

* **请求顶层字段**(不再是 optimizer_params):
  `anchor_frames: list[int]`(1–16 个唯一数据集帧索引,空 = 无目标)、
  `region_radius: float`(默认 15,稳健缩放单位,实测校准:单次变异
  漂移 13–18)。
* **优化器侧**:TargetRegionOptimizer 类删除;定向机制原样平移进
  `RandomSearchOptimizer` 的独立分支(`_propose_targeted` /
  `_observe_targeted`),`context.anchor_descriptors` 非空时激活:
  距离加权父选择 `exp(-(d/r)²)`、15% 移民份额( sweep 验证值,内部常量
  `_TARGET_IMMIGRANT_SHARE`)、accepted 池按**最近保留**剪枝(区域填充
  的前沿保持可育)。非锚点路径(均匀采样、reuse FPS 池)逐行未动。
* **门控**(parse_request):锚点当前仅支持 random 优化器(GA/PSO 的
  距离加权语义未定义、未实测,明确拒绝而非静默改行为);
  `reuse_accepted_seeds` 与锚点互斥(reuse 的多样性剪枝与定向的
  最近保留剪枝语义冲突)。context 契约新增 `region_radius`。
* **UI**:方法下拉恢复三项(random/genetic/pso);新增独立
  **TARGET REGION**(目标区域)区块(锚点帧 + 半径),置于优化器与预算
  之间——搜索目标从此与方法并列选择,可组合性由表单结构保证。
  (初版区块名 "SEARCH TARGET" 与第 2 区块 "SEARCH OBJECTIVE" 在中文里
  同为"搜索目标",已改名。)选项间联动:锚点设置时 genetic/pso 下拉项
  禁用、reuse 开关强制关闭并禁用;切到 genetic/pso 时锚点输入禁用——
  后端门控前移到表单,组合错误在选项层就不可能发生。
* service worker:锚点块按 `request.anchor_frames` 触发(不再看
  optimizer 名),强制入池/描述符计算/锚点几何校验逻辑不变。

## 行为保持验证

* 定向路径冒烟:接近度中位数 13.6(重构前 TR 机制同参数一致)。
* Random golden 基线逐位不变(无锚点 → 定向分支不可达);
  `tests/test_generation_optimizers.py` 全过。
* 新测试 `tests/test_generation_search_target.py`(13 个):激活门控、
  集中性、round size/budget、移民广度、可复现、池溢出最近保留、
  外部候选隔离、请求归一化与门控、引擎集成。
* 全量:后端 **580 passed + 1 skipped**;前端 tsc/eslint/**229 passed**。
* benchmark harness:`--optimizers target_region` 标签保留(报告连续),
  实际构建 random + 锚点 + r=15。

## 语义提示

* "random + 搜索目标"即原 target_region 的全部实测行为
  (20/20 接近度全胜,discovery −6%)——G5-2 的结论原样迁移。
* GA/PSO + 锚点是**已识别的扩展位**(context 管道就绪),待各自定义
  距离加权语义并实测后再放开门控。
