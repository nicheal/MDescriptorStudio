# 保存调度问题分析与修复计划文档

不做任何代码改动,仅创建一个文档:

**文件**:`docs/plan/scheduling-fix-plan.md`(docs/plan 下已有 engine-known-issues.md 等非编号文档,命名风格一致)

**内容**(整合此前两轮输出的完整内容,自包含可独立阅读):

1. **问题分析**(8 项,含 file:line 引用与 P0/P1/P2 分级)
   - P0:僵尸计算占满工作线程、关停不协作取消且可能挂住进程、调度器无类型感知
   - P1:`compute.default_threads` 死设置、descriptor.compute 无在途去重、engine.update 与 compute 缺互斥、重计算期间 RPC 饥饿、heatmap 全量加载
   - P2:RUNNING 更新缺守卫、取消检查点缺口、values 重复加载等

2. **修复方案**(8 个改动项,含具体文件与实现要点)
   - 类别线程池(engine 1 / analysis 2 / dataset 2,update 与 compute 互斥)
   - 协作式关停 + os._exit + RUNNING 守卫
   - 取消检查点 + reduceat 向量化
   - descriptor 在途去重
   - 控制通道扩容 + heatmap mmap
   - default_threads 接线(threadpoolctl,已确认方案)
   - 前端队列位置显示
   - ADR-27 + 设计文档更新

3. **测试计划、实施顺序、暂缓项说明**(values LRU 缓存、僵尸池可视化)

文档标题注明日期(2026-09-06)与依据(基于 commit a55cef9 "Refine Jobs" 时的代码状态)。