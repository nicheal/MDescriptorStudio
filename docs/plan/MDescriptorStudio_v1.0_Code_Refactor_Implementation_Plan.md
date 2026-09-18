# MDescriptorStudio v1.0 代码重构实施计划

## 目标

将 MDescriptorStudio
从描述符分析工具升级为面向材料模拟和机器学习势开发的科学计算平台。

目标架构：

    Data Model
        |
    Structure Layer
        |
    Descriptor Engine
        |
    Analysis Framework
        |
    ML Workflow
        |
    Visualization

------------------------------------------------------------------------

# 重构原则

1.  保持现有功能稳定。
2.  小步迁移，避免一次性重写。
3.  新旧接口并存。
4.  每个阶段增加自动化测试。

------------------------------------------------------------------------

# Epic-001 Backend核心服务拆分

## 当前问题

核实后的真实缺口（按严重度）：

-   `services/dataset_service.py` 1099 行 —— 视图 CRUD（约 235 行）与
    `periodic_boundary_ghosts` 几何辅助可从数据生命周期中拆出 —— 已修复，见下文
-   `services/descriptor_service.py` 654 行 —— 复核后判定为单一关注点
    （提交、缓存键、作业编排围绕同一次 descriptor_run），不再拆分
-   `analysis/algorithms/_common.py` 640 行 —— 共享预处理与有界搜索工具，
    拆散会让各算法各自复制一份，保持不变

`main.py` 仅 259 行，不构成风险；原清单把它与"职责过重"一并列为债属误判。
DescriptorService / DatasetService 的"过胖"只在 DatasetService 上成立。

## 目标结构

    backend/

    bootstrap/
        runtime.py
        services.py

    services/

    descriptor/
        service.py
        cache.py
        validator.py
        runner.py

    dataset/
        registry.py
        fingerprint.py
        lineage.py

## 实施结果（已完成）

按关注点拆出三个模块，`main.py` 逐一接线：

    services/
        dataset_service.py          690 行  注册表 + 指纹一致性 + 健康统计
        dataset_view_service.py     286 行  不可变帧选择视图（CRUD / split / materialize）
        dataset_frame_service.py    125 行  单帧读取，供 Explore 查看器成形
    datasets/
        ghosts.py                    74 行  周期性镜像原子（纯几何，独立测试）

`dataset.view.*` 与 `dataset.frame` 的 RPC 名称和响应形状均未改动，前端零改动；
255 项后端测试保持全绿。

未采纳原拟的 `descriptor/{service,cache,validator,runner}.py` 与
`dataset/{registry,fingerprint,lineage}.py` 分层拆分：那些代码各自只服务一个
关注点，按技术层次切分会把同一份状态（适配器缓存、扫描锁）撕散到多个文件，
可读性与可测性都变差。留此记录，勿再按原结构开工。

## 验收

-   一个 Service 只承担一个关注点；行数是启发式而非硬指标 ——
    `DatasetService` 保留 690 行，因为健康统计与注册表共享适配器缓存和扫描锁，
    强行拆开会让设计变差
-   核心逻辑具备单元测试（255 项通过，重构须保持全绿）

------------------------------------------------------------------------

# Epic-002 Job系统重构 —— 已完成，勿再实施

`services/job_service.py` 已实现比本计划更严格的方案：

    QUEUED → RUNNING → COMPLETED / FAILED / CANCELLED

-   取消一致性：`cancel()` 立即结算 job 与其关联 run 行并 detach runner；
    所有终态写入带 `WHERE status IN ('QUEUED','RUNNING')` 守卫，杜绝
    CANCELLED → RUNNING 复活与重复 `job.finished`
-   崩溃恢复：构造时 `_sweep_zombie_runs("backend_restart")` 关闭上次会话遗留的非终态行
-   孤儿任务：`_settle_linked_runs` 级联结算 `descriptor_runs` / `analysis_runs`
-   优雅退出：`shutdown()` 先协作取消存活作业，超时后再次清扫

本计划原拟新增 `CREATED`、`CANCEL_REQUESTED` 两个状态与
`cancel_requested_at` / `worker_id` / `error_trace` 三列 —— **已否决**：
引入 `CANCEL_REQUESTED` 中间态会重新打开当前设计刻意关闭的竞态窗口
（取消与"开始运行"交错时状态可能被覆写），而错误信息已由 `jobs.error`
与 `error_id` 承载，单进程内 `worker_id` 无消费者。

测试已覆盖本计划列出的四类场景：正常完成、用户取消、backend 崩溃、重启恢复
（`tests/test_job_cancel.py`、`test_job_scheduling.py`、`test_run_settlement.py`、
`test_job_result_persistence.py`）。

------------------------------------------------------------------------

# Epic-003 科学数据模型

新增：

    core/

    StructureFrame
    Trajectory
    DatasetObject
    DescriptorObject
    FeatureMatrix

统一结构：

    positions
    atomic_numbers
    cell
    pbc
    properties

所有格式：

-   EXTXYZ
-   POSCAR
-   LAMMPS
-   DeepMD

转换为统一对象。

------------------------------------------------------------------------

# Epic-004 Descriptor系统升级

目标支持：

-   SOAP
-   ACSF
-   ACE
-   MACE
-   DeepMD

结构：

    descriptor/

    core/
        descriptor.py
        schema.py
        capability.py

    backend/
        soap.py
        ace.py
        mace.py
        deepmd.py

新增 DescriptorSchema：

包含：

-   cutoff
-   symmetry
-   periodic
-   body order
-   version

------------------------------------------------------------------------

# Epic-005 大规模数据能力

解决百万级MD轨迹问题。

新增：

    storage/

    trajectory_index.py

    chunk_store.py

    zarr_store.py

支持：

-   lazy loading
-   chunk读取
-   streaming统计

------------------------------------------------------------------------

# Epic-006 Analysis框架升级

ScientificWarning 已实现：`analysis/algorithms/` 共 23 处 `warnings.append`
随每次结果返回（零方差特征、常量特征、采样受限、维度过高等）。

剩余缺口：

新增统一：

    AnalysisObject

包含：

-   输入描述符
-   参数
-   算法版本
-   结果
-   统计信息

（`analysis_runs` 已保存 `params_json`、`result_path`、`cache_key` 与
`algorithm_version`（当前 `studio-analysis-4`），本 Epic 的目标已基本达成；
剩余只是把这些字段聚合成显式的 `AnalysisObject`。）

------------------------------------------------------------------------

# Epic-007 ML势工作流

新增：

    ml/

    model_registry.py

    training.py

    selection.py

    validation.py

    active_learning.py

流程：

    Dataset

    ↓

    Descriptor

    ↓

    Selection

    ↓

    Training

    ↓

    Validation

    ↓

    Uncertainty

    ↓

    New Dataset

------------------------------------------------------------------------

# Epic-008 Benchmark体系

新增：

    benchmark/

    descriptor/

    analysis/

    storage/

测试规模：

-   1000 structures
-   10000 structures
-   100000 structures

指标：

-   runtime
-   memory
-   CPU scaling
-   GPU scaling

------------------------------------------------------------------------

# Epic-009 测试体系升级

目录：

    tests/

    unit/

    integration/

    scientific/

    regression/

增加：

-   descriptor数值回归 —— 已有：`tests/regression/descriptor.npy` + `expected.npz`
-   PCA/kernel 结果回归 —— 已有：`tests/regression/test_analysis_regression.py`
-   随机化与模糊测试 —— 已有：`tests/numerical/test_analysis_hypothesis.py`、
    `test_analysis_fuzz.py`

本 Epic 的真实缺口只有目录分层（unit / integration 尚未分目录）与
Benchmark，而非测试本身。

------------------------------------------------------------------------

# 推荐开发顺序

## 第一阶段

完成：

-   StructureFrame
-   ~~拆分超大模块~~ —— `dataset_service.py` 已拆至 690 行（Epic-001）；
    剩余 `Analysis.tsx`

（Job 状态机与 Matrix Budget Manager 经核实均已实现，已从本阶段移除；
详见 Epic-002 的否决说明。）

## 第二阶段

完成：

-   DescriptorObject
-   Descriptor插件化
-   Dataset lineage

## 第三阶段

完成：

-   ML Workflow
-   Active Learning
-   Benchmark

## 第四阶段

准备：

CPC/JOSS发表版本。

------------------------------------------------------------------------

# 最终目标

MDescriptorStudio成为：

    材料结构数据管理

    +

    机器学习势描述符分析

    +

    数据选择

    +

    模型训练

    +

    主动学习闭环

的一体化科学计算平台。
