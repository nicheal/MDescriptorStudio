# MDescriptorStudio v1.0 重构路线图

## 目标

将 MDescriptorStudio
从描述符分析工具升级为面向材料模拟与机器学习势开发的科学计算平台。

------------------------------------------------------------------------

# 当前状态

现有架构：

Frontend (React) → Tauri IPC → Python Backend → Dataset / Descriptor /
Analysis

优势：

-   桌面软件架构完整
-   Descriptor插件思想明确
-   数据版本管理意识较强
-   分析模块较丰富

主要技术债：

1.  前端 `pages/Analysis.tsx` 2093 行，其中单个组件占 1598 行
2.  大规模数据处理能力不足（无 lazy loading / chunk 存储 / 流式统计）
3.  ML势训练闭环不完整

（原列首位的 `services/dataset_service.py` 1099 行已按关注点拆至 690 行，
见 Code_Refactor_Implementation_Plan Epic-001。）

已不再是债（原清单误列为待偿）：核心计算接口不统一、描述符缓存、
大矩阵内存守卫、科学警告层、结果 provenance 与版本记录 —— 均已在现有代码中实现。

------------------------------------------------------------------------

# Phase 0：稳定化版本（v0.8）—— 已完成

原计划用于降低高风险技术债的三项均已在现有代码中落地，Phase 0 关闭。

## 1. Job系统 —— 已实现

`services/job_service.py` 已闭环：

    QUEUED → RUNNING → COMPLETED / FAILED / CANCELLED

-   任务取消一致性：取消即刻结算 job 与关联 run 行，并用状态守卫的
    UPDATE 阻止 CANCELLED → RUNNING 复活与重复 `job.finished`
-   崩溃恢复：构造时清扫上次会话遗留的非终态行
-   孤儿任务：失败/取消会级联结算 `descriptor_runs` / `analysis_runs`

## 2. 大矩阵内存守卫 —— 已实现

-   kernel 矩阵：`max_samples` 采样上限（默认 400、硬顶 2000），确定性采样并附告警
-   最近邻/参考搜索：显式有界内存实现，仅保留每查询 top-k
-   峰值内存记账：`descriptor_runs.memory_peak_bytes`

block 分块计算仍属可选优化，不是缺口。

## 3. 科学警告层 —— 已实现

`analysis/algorithms/` 共 23 处 `warnings.append`，随每次结果返回
（零方差特征、常量特征、采样受限、特征维度过高等）。

------------------------------------------------------------------------

# Phase 1：科学对象模型升级（v0.9）

## StructureFrame

统一结构表示：

-   positions
-   atomic_numbers
-   cell
-   pbc
-   properties

所有格式：

EXTXYZ POSCAR LAMMPS DeepMD

转换到统一对象。

------------------------------------------------------------------------

## DescriptorObject

替代简单numpy矩阵。

包含：

-   descriptor定义
-   参数
-   版本
-   数值
-   来源信息

------------------------------------------------------------------------

## FeatureMatrix

作为：

Descriptor → Analysis → ML

统一接口。

------------------------------------------------------------------------

# Phase 2：Descriptor平台化（v1.0）

目标：

支持：

-   SOAP
-   ACSF
-   ACE
-   MACE
-   DeepMD

架构：

descriptor/

core/

-   descriptor.py
-   schema.py
-   capability.py

backend/

-   soap.py
-   ace.py
-   mace.py
-   deepmd.py

------------------------------------------------------------------------

# Phase 3：机器学习势闭环（v1.2）

建立：

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

Active Learning

↓

New Dataset

------------------------------------------------------------------------

新增：

-   Model Registry
-   Training Pipeline
-   Error Analysis
-   Active Learning

------------------------------------------------------------------------

# Phase 4：发表级工程化（v1.5）

达到科学软件发表要求。

增加：

## Benchmark

测试：

-   1000 structures
-   10000 structures
-   100000 structures

指标：

-   runtime
-   memory
-   scalability

------------------------------------------------------------------------

## Regression Test

保存：

-   descriptor reference
-   PCA reference
-   kernel reference

保证算法升级结果稳定。

------------------------------------------------------------------------

# 最终目标架构

Dataset Layer

↓

StructureFrame

↓

Descriptor Layer

↓

SOAP / ACE / MACE

↓

Analysis Layer

↓

PCA / Kernel / Similarity

↓

ML Layer

↓

Training / Validation / Active Learning

↓

Visualization

------------------------------------------------------------------------

# 优先级

P0：

-   拆分超大模块：`pages/Analysis.tsx`（2093 行）
-   StructureFrame 统一结构对象
-   Benchmark（发表硬门槛，当前完全缺失）

P1：

-   DescriptorObject / FeatureMatrix
-   大规模轨迹存储（lazy loading、chunk 读取、流式统计）
-   Model Registry

P2：

-   Active Learning
-   Dataset lineage 图形化呈现（血缘数据已在库中）
-   高级可视化

已从 P0 移除（核实后确认已实现）：Job 状态机、大矩阵内存守卫、科学警告层。
