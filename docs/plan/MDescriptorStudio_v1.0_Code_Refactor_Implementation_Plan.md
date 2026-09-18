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

主要风险：

-   main.py职责过重
-   DescriptorService过胖
-   DatasetService过胖

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

## 验收

-   main.py控制在100行以内
-   单个Service低于300行
-   核心逻辑具备单元测试

------------------------------------------------------------------------

# Epic-002 Job系统重构

## 新状态机

    CREATED
       |
    QUEUED
       |
    RUNNING
       |
    CANCEL_REQUESTED
       |
    CANCELLED

    RUNNING
       |
    COMPLETED

## 修改内容

新增：

    jobs/state_machine.py

数据库增加：

-   cancel_requested_at
-   worker_id
-   error_trace

测试：

-   正常完成
-   用户取消
-   backend崩溃
-   重启恢复

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

新增统一：

    AnalysisObject

包含：

-   输入描述符
-   参数
-   算法版本
-   结果
-   统计信息

增加：

ScientificWarning：

检测：

-   PCA解释不足
-   样本不足
-   高维风险
-   内存风险

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

-   descriptor数值回归
-   PCA结果回归
-   kernel结果回归

------------------------------------------------------------------------

# 推荐开发顺序

## 第一阶段

完成：

-   Job状态机
-   StructureFrame
-   Matrix Budget Manager

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
