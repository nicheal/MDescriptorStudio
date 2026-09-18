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

1.  核心Service类过度集中
2.  科学对象模型不足
3.  大规模数据处理能力不足
4.  ML势训练闭环不完整

------------------------------------------------------------------------

# Phase 0：稳定化版本（v0.8）

目标：

降低高风险技术债。

## 1. Job系统重构

建立完整状态机：

CREATED → QUEUED → RUNNING → CANCEL_REQUESTED → CANCELLED / COMPLETED

解决：

-   任务取消一致性
-   崩溃恢复
-   孤儿任务

------------------------------------------------------------------------

## 2. Matrix Budget Manager

统一管理：

-   kernel matrix
-   distance matrix
-   correlation matrix
-   PCA输入矩阵

支持：

-   内存预测
-   自动采样
-   block计算

------------------------------------------------------------------------

## 3. Scientific Warning Layer

增加科学解释提醒：

例如：

-   PCA解释方差不足
-   样本数量不足
-   特征维度过高
-   相关分析可靠性不足

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

-   Job状态机
-   Descriptor科学模型
-   StructureFrame
-   大矩阵管理

P1：

-   FeatureMatrix
-   Model Registry
-   Dataset lineage graph

P2：

-   Active Learning
-   Benchmark
-   高级可视化
