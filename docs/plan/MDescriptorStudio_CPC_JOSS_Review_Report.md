# MDescriptorStudio CPC/JOSS软件发表级审查报告

## 软件定位

MDescriptorStudio定位：

面向机器学习势函数开发的描述符分析与材料结构数据管理平台。

------------------------------------------------------------------------

# 软件优势

## 1. 工程架构

优点：

-   React + Tauri桌面架构
-   Python科学计算后端
-   模块化Descriptor接口

------------------------------------------------------------------------

## 2. 科学计算能力

已有：

-   Descriptor分析
-   PCA降维
-   Kernel分析
-   特征统计

具备进一步扩展基础。

------------------------------------------------------------------------

# 需要提升的方面

## 1. 可复现性

已具备：

-   版本记录：`descriptor_runs.engine_version` 与逐描述符 `descriptor_version`
-   参数记录：`parameters_json` 全量入库，并计入缓存键
-   descriptor provenance：`scope`、`frame_index`、`output_dtype`、`device`、
    `memory_peak_bytes` 一并保存
-   数据血缘：`analysis_runs.descriptor_run_id` 外键 + `dataset_views` 派生关系

仍需补充：

-   可导出的 provenance 报告：版本、参数与 `algorithm_version`
    （`studio-analysis-4`）已在库内，但未随结果文件落盘

------------------------------------------------------------------------

## 2. Benchmark

必须提供：

数据规模：

-   小规模
-   中规模
-   大规模

比较：

-   速度
-   内存
-   并行效率

------------------------------------------------------------------------

## 3. 测试体系

已建立并全绿（255 项通过）：unit、integration、numerical regression
（`tests/regression/`：`descriptor.npy` + `expected.npz` 基线）、
随机化与模糊测试（`tests/numerical/`：hypothesis + fuzz）。

仍缺：scientific validation test —— 与文献或参考实现的数值对照。

------------------------------------------------------------------------

## 4. 文档体系

需要：

architecture.md

developer guide

API documentation

benchmark report

tutorial examples

------------------------------------------------------------------------

# 建议论文展示内容

## 软件架构图

展示：

Dataset

Descriptor

Analysis

ML Workflow

## 性能测试

展示：

不同数据规模下：

-   时间
-   内存
-   扩展性

## 科学案例

建议：

案例1：

SOAP/ACE描述符空间分析

案例2：

结构多样性采样

案例3：

主动学习数据选择

------------------------------------------------------------------------

# 发表前关键任务

1.  增加 Benchmark：1k / 10k / 100k 结构的 runtime、memory、并行效率 ——
    当前完全缺失，是发表的首要门槛
2.  提供实际材料体系案例
3.  补齐开源发布要件：根目录 LICENSE、README、CHANGELOG、pyproject.toml
4.  拆分超大前端模块：`pages/Analysis.tsx`（2093 行）
5.  完善开发文档

原清单第 1、3 项已不再需要列入："完成 v1.0 架构重构"与"增加完整测试"——
后端已有 32 个测试文件（含 numerical 与 regression 两层，255 项通过）、
CI 已在运行、可复现性字段已入库。

------------------------------------------------------------------------

# 结论

MDescriptorStudio已经具备科学软件雏形。

进一步提升方向：

从：

Descriptor visualization tool

升级为：

Machine-learning potential development platform
