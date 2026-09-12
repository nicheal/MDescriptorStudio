# 计算调度问题分析与修复计划

- 日期:2026-09-06
- 代码基线:commit `a55cef9`(Refine Jobs)
- 状态:已实施(资源感知线程池见 job_service.py `_POOL_SIZES`)

## 一、现状概述

调度核心是「2 线程 FIFO + 协作式取消」的最小实现:stdio NDJSON RPC(4 worker + `job.cancel` 控制通道,`protocol/server.py`),`JobService` 用 `ThreadPoolExecutor(max_workers=2)` 跑所有任务(`services/job_service.py:95`),SQLite WAL 单写者,引擎计算为同步调用。结算(首次 finalize 语义)、重启/关停僵尸行清理、控制通道等正确性问题在 a55cef9 已打磨到位;剩余问题集中在**缺少类型感知与资源感知**。

## 二、问题清单

### P0 — 会卡死调度池的问题

**1. 取消后的「僵尸计算」可以占满全部工作线程**
`cancel()` 立即结算数据库行并 detach(`services/job_service.py:249-270`),但 detach 不会停止线程——任务继续阻塞在原生调用里(t-SNE fit、UMAP/numba、SVD)直到自然返回。工作池只有 2 个线程,**两个被取消但卡在原生调用里的任务就能让整个 job 池停摆**:后续所有任务永远 QUEUED,且没有机制区分「正常排队」和「池被僵尸占满」,也没有 watchdog。

**2. 关停路径不协作取消,且可能挂住进程退出**
`shutdown()`(`services/job_service.py:289-304`)只 `cancel_futures`(未启动的任务),从不调用运行中任务的 `ctx.cancel()`;等 3 秒后 sweep 数据行并 `db.close()`。仍在跑的 job 线程(此时并未 detach)之后任何 `db.execute`/`ctx.progress` 都会打到已关闭的连接上。更严重的是 CPython 3.9+ 的 `ThreadPoolExecutor` 线程是非 daemon 的,解释器退出时会 join 所有线程——一个卡在原生调用里的计算会**阻止后端进程退出**。

**3. 调度器完全无类型感知**
所有 job 共享一个 FIFO 队列、固定 2 并发:重计算(`descriptor.compute`、`analysis.umap/tsne`)、轻任务(`dataset.statistics`、`dataset.export`)、网络型(`engine.update` 的 pip 下载)同权重。后果:
- 两个重计算并行时 CPU(无线程数配置,全靠 BLAS 默认)和内存双双争用——每个 compute 把全部 frames 逐个 append 进内存(`services/descriptor_service.py:473-486`),2GB 输入预算是**单任务**的,并发的总内存无准入控制;
- 一个轻量统计任务可能排在两个长计算后面出不来,无优先级、无队列位置展示。

### P1 — 功能性缺陷

**4. `compute.default_threads` 是个死设置**
`main.py:42` 允许写入、`SettingsDrawer.tsx` 暴露给用户(InputNumber 1–64),但后端没有任何地方读取它。分析引擎全部硬编码 `n_jobs=1`(如 `analysis/engine.py:729` UMAP、`engine.py:236/307/792` 近邻/距离),描述符计算也没有 torch/OMP 线程配置。用户设置完全无效,实际并行度由 BLAS 默认行为决定。

**5. `descriptor.submit` 没有在途去重**
只查 `status = 'COMPLETED'` 的缓存命中(`services/descriptor_service.py:214-222`),同一 cache_key 已有 QUEUED/RUNNING 任务时照样再排一个完整计算。对比 analysis 路径有 active 去重(`services/analysis_service.py:615-626`)。双击或双入口提交会把同一个重计算跑两遍。

**6. `engine.update` 与计算任务缺互斥**
`update_runner` 直接 `pip install --upgrade mdescriptor`(`services/update_service.py:116-137`)。2 线程池下它可以和 `descriptor.compute` 并行;Windows 上引擎的 .pyd 正被加载锁定,pip 会失败或装出不一致状态。调度器不知道这两类 job 冲突。

**7. 重计算期间 RPC 通道饥饿,进度会停更**
job 线程里存在长 GIL 段:PCA 逐点构建 2 万条 payload(`services/analysis_service.py:162-176`)、`_pool_per_structure` 的 Python 逐结构循环(`services/analysis_service.py:315-320`)、atom 模式百万级 `sample_ids` 字符串列表(`services/analysis_service.py:1078`)。RPC 池只有 4 线程,前端 `watchJob` 每 500ms 轮询 `job.get`(`frontend/src/stores/jobs.ts:143`),`jobs.ts:156-160` 的 BUSY 退避注释已自认了这个拥塞。但控制通道只保留了 `job.cancel`(`protocol/server.py:26`)——**`job.get` 不在保留通道上,重负载下进度条会停更**。

**8. `result.heatmap` 在 RPC 线程上全量加载矩阵**
`heatmap` 每次调用 `load_values` 把整个 `values.npy` `np.load` 进内存(`services/result_service.py:194`,内部在 `result_service.py:108-117`),只为切出一个结构的小块——帧浏览场景下每帧一次大 I/O,无 mmap、无缓存,还会挤占 RPC worker。

### P2 — 竞态与细节

- **RUNNING 更新缺状态守卫**:`_run` 的 `UPDATE jobs SET status='RUNNING' ... WHERE id=?`(`services/job_service.py:162-164`)没有状态守卫,与 `cancel()` 的 finalize 并发时出现 CANCELLED→RUNNING→CANCELLED 的状态回闪,且 `job.finished` 会被双发(`_finalize` 的 WHERE 守卫对 RUNNING 会再次命中)。
- **取消检查点覆盖不足**:`_load_samples`(`services/analysis_service.py:1042-1117`)和 `_pool_per_structure` 的长循环内没有 `ctx.check_cancelled()`,取消只能等到阶段边界才生效。
- **同一 run 的 values 重复加载**:`load_values` 无缓存,`_load_samples` 里 `np.asarray(values, dtype=np.float64)`(`services/analysis_service.py:1046`)对 float32 存储的结果会复制一份;并发 N 个分析同一 run 就是 N 份全量矩阵。
- `_pca_submit_lock` 一把锁管 pca/generic/export 三条提交路径,命名有误导性(影响极小)。

## 三、修复方案(8 个改动项)

### 改动 1:JobService 类别线程池(修 P0-3、P1-6)

`backend/mdescriptor_studio_backend/services/job_service.py`:
- 单一 `_executor` 改为按类别的 3 个池(模块级常量 `_POOL_SIZES`,便于调整):
  - `"engine"`(max_workers=1):`descriptor.compute`、`engine.update` —— 共享单 worker 天然实现互斥:pip 升级引擎期间描述符计算排队,反之亦然;
  - `"analysis"`(max_workers=2):所有 `analysis.*`;
  - `"dataset"`(max_workers=2,兜底):`dataset.*` 及未知类型。
- `_category(job_type)` 按前缀映射;`submit()` 内部路由,签名与队列背压 `_queue_slots`(64)不变;`_run`/取消/结算逻辑全部不动。
- 队列位置可见:`_queue_positions()` 按「同类别 QUEUED、按 created_at,id 排序」计算序号,`list_jobs` 与 `get_job` 的 QUEUED 行附带 `queue_position` 字段。

### 改动 2:协作式关停 + RUNNING 守卫(修 P0-2、P2-9)

- `shutdown()`:先对 `_contexts` 中所有存活 context 调 `ctx.cancel()`(触发引擎 ComputeControl.cancel)+ detach(复用现有 cancel 路径,连 run 行结算一起正确完成),再对各池 `shutdown(wait=False, cancel_futures=True)`,保留 3s 有界等待与现有 sweep。
- `main.py`:finally 中 `jobs.shutdown()` → `db.close()` → `logging.shutdown()` 后 `os._exit(0)`,绕过解释器退出时对非 daemon executor 线程的 join——卡在原生调用里的线程不再阻止进程退出(此为 stdio sidecar,父进程本就会终止它)。
- `_run` 中的 `UPDATE jobs SET status='RUNNING'` 补 `AND status='QUEUED'` 守卫。

### 改动 3:取消检查点 + 池化循环向量化(缓解 P0-1、修 P2-10、缓解 P1-7)

`backend/mdescriptor_studio_backend/services/analysis_service.py`:
- 新增共享 `_pool_rows(values, offsets)`:`np.add.reduceat` 一步完成逐结构均值(空组掩码置 0),替换 `_pool_per_structure`(约 315-320 行)与 `_load_samples` structure 分支(约 1085-1093 行)的两处 Python 逐行循环——更快且大幅缩短 GIL 占用段。
- `_load_samples` / `_pool_per_structure` 增加可选 `check` 回调(调用方传 `ctx.check_cancelled`),在加载、池化等长阶段之间设置检查点。

### 改动 4:descriptor.compute 在途去重(修 P1-5)

`backend/mdescriptor_studio_backend/services/descriptor_service.py:submit`:仿照 analysis 路径——新增 `_submit_lock`,锁内先查 COMPLETED 缓存(现状),再查 `cache_key` 相同且 status IN ('QUEUED','RUNNING') 且有存活 job 的 run;命中则返回 `{job_id, cache:{existing_run_id, cache_key}}` 不再新建;`force=True` 绕过两者。失败回滚逻辑保持。

### 改动 5:控制通道扩容 + heatmap mmap(修 P1-7、P1-8)

- `backend/mdescriptor_studio_backend/protocol/server.py:26`:`CONTROL_METHODS` 增加 `job.get`、`job.list`(保留通道现有 1 worker/8 slots 对这两个快查询足够),重计算拥塞时进度轮询与取消不再被饿死;前端既有 BUSY 退避保留为兜底。
- `backend/mdescriptor_studio_backend/services/result_service.py`:`load_values(run_id, *, mmap=False)`;`heatmap` 改用 `mmap_mode="r"`,帧浏览不再每次全量读入 values.npy。

### 改动 6:`compute.default_threads` 接线到分析计算(修 P1-4,已确认方案)

- `analysis_service._run_engine` 开头读取 `compute.default_threads`(防御性解析,非法/空则忽略),>0 时用 `threadpoolctl.threadpool_limits(limits=n)` 包裹本次引擎调用(不恢复,进程级保持用户选择;单用户桌面场景可接受,注释说明)。
- `backend/requirements.txt` 显式添加 `threadpoolctl`(现为 sklearn 传递依赖,版本按 .venv 实装版本固定)。
- `SettingsDrawer.tsx` 补充说明文案:该设置影响分析计算,描述符引擎线程由引擎自身管理。

### 改动 7:前端队列位置显示

- `frontend/src/types/protocol.ts` JobRow、`frontend/src/stores/jobs.ts` JobState 增加 `queue_position?: number`,`fromJobRow` 透传(live 事件不含该字段时保持可选,merge 展开不会误清)。
- `frontend/src/components/JobsDrawer.tsx` JobCard:QUEUED 态在「排队中」旁显示「第 N 位」(i18n 新增 zh "第 {n} 位" / en "#{n} in queue")。

### 改动 8:文档

- `docs/plan/01-DECISIONS.md` 新增 **ADR-27「Job 类别线程池与协作式关停」**(背景/决策/后果,按现有格式;编号接现有 ADR-26)。
- `docs/plan/03-BACKEND_DESIGN.md` §7「JobService 默认并发为 2」改为类别池描述(engine 1 / analysis 2 / dataset 2,update 与 compute 互斥)。

## 四、测试计划

新增 `tests/test_job_scheduling.py`(沿用 `tests/test_run_settlement.py` 的真实 Database+JobService+假 runner 模式):

- 类别隔离:engine 池被阻塞时 dataset 任务正常完成;
- 互斥:`descriptor.compute` 运行中提交 `engine.update` 保持 QUEUED,compute 结束后才启动;
- 协作式关停:运行中任务在 `shutdown()` 后变为 CANCELLED('backend_shutdown')且 runner 收到取消;
- 队列位置:池占满后多个排队任务的 `queue_position` 正确;
- 描述符在途去重:同参数二次 submit 返回既有 job_id,force 创建新 run。

`frontend/src/stores/jobs.test.ts` 补 `fromJobRow`/merge 对 `queue_position` 的用例。

回归:后端 `pytest tests/ -q` 全量;前端 typecheck + vitest。

## 五、实施顺序

1 → 2 → 4 → 3 → 5 → 6 → 7 → 8,每步后跑对应测试;最后全量回归。

## 六、说明与暂缓项

- 并发上限从全局 2 变为按类别(最坏 1+2+2=5),类别内 FIFO 不变;重计算之间的资源争用由「engine 池串行 + 分析 BLAS 线程上限」共同约束。
- **values 结果 LRU 缓存暂缓**:分析池隔离 + heatmap mmap 已消除主要重复加载场景,待实际使用中确认仍有需求再加(避免引入内存上限管理复杂度)。
- **僵尸池占用状态的前端可视化暂缓**:类别隔离 + 检查点已把最坏影响限制在 engine 池内,`queue_position` 也能暴露「长期无进展」信号。
- 单测中的 Fake `_Jobs`(`tests/test_analysis_cache.py:13-42` 等)依赖 `submit(job_type, runner, *, dataset_id=None, analysis_run_id=None)` 契约,本方案保持 `submit` 签名不变,兼容无损。
