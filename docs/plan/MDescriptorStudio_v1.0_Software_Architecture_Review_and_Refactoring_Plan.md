# MDescriptorStudio v1.0 Software Architecture Review and Refactoring Plan

## 1. 文档目标

本文档用于连接：

    Current Code Base

    ↓

    Architecture Analysis

    ↓

    Refactoring Plan

    ↓

    MDescriptorStudio v1.0 Target Architecture

目标：

解决当前软件存在的：

-   模块耦合
-   代码重复
-   扩展困难
-   测试不足
-   科学计算接口不统一

等技术债务问题。

------------------------------------------------------------------------

# 2. 当前架构评估

本文档初版所列问题多数已在后续开发中消除。以下为按当前代码逐条复核后的结论。

## 2.1 模块边界

已解决：

-   UI 与计算分离：React 前端与 Python 后端是两个进程，仅经 IPC 协议通信
-   科学计算不依赖 GUI：后端可独立运行与测试（255 项后端测试无前端参与）
-   分析功能已分层：`analysis/algorithms/`、`analysis/sampling/`、`analysis/metrics/`

仍存在：

-   `pages/Analysis.tsx` 2093 行 —— 单个组件 1598 行

（原列于此的 `services/dataset_service.py` 1099 行已按关注点拆至 690 行。）

------------------------------------------------------------------------

## 2.2 核心计算接口

已由引擎契约解决，不需要再建 `DescriptorBase` 抽象层：

-   引擎输出版本化 JSON schema（28 个描述符 / 170 个参数）与能力位
    （`input.mixed_periodicity`、`execution.devices`、`execution.cooperative_cancel`）
-   `mdescriptor_adapter.py` 按名字动态派发：
    `list_descriptors()` → `describe_descriptor(name)` → `create_descriptor(cfg)`
-   新增描述符不需要改动 Studio 代码；前端直接消费引擎 schema，
    已不再维护参数名映射表

------------------------------------------------------------------------

## 2.3 Workflow

已与具体模块解耦：作业由 `JobService` 按类别线程池调度，状态机、取消一致性
与崩溃恢复闭环（详见 Code_Refactor_Implementation_Plan Epic-002）。

DAG、checkpoint 与错误恢复仍未实现 —— 属新功能缺口，不是债务清偿。

------------------------------------------------------------------------

# 3. 重构总体原则

## 原则1：模块化

采用：

    Core

    +

    Plugin

    +

    Service

结构。

------------------------------------------------------------------------

## 原则2：科学计算与界面分离

目标：

    GUI

    ↓

    API

    ↓

    Scientific Core

------------------------------------------------------------------------

## 原则3：数据对象统一

建立：

    MaterialObject

    StructureObject

    DatasetObject

    DescriptorObject

    ResultObject

------------------------------------------------------------------------

## 原则4：插件优先

所有扩展：

通过：

    Plugin Interface

实现。

------------------------------------------------------------------------

# 4. 目标架构

    MDescriptorStudio

    |

    ├── core/

    │   ├── data

    │   ├── workflow

    │   ├── descriptor

    │   └── analysis


    ├── plugins/

    │   ├── soap

    │   ├── ace

    │   ├── ml

    │   └── visualization


    ├── services/

    │   ├── database

    │   ├── scheduler

    │   └── report


    ├── interface/

    │   ├── GUI

    │   └── API


    └── tests/

------------------------------------------------------------------------

# 5. 核心模块重构

## 5.1 Data Layer

统一：

    Dataset

    Structure

    Metadata

    Result

支持：

-   JSON
-   HDF5
-   Zarr

------------------------------------------------------------------------

## 5.2 Descriptor Layer

设计：

    BaseDescriptor

          |

    ----------------

    SOAP

    ACE

    ACSF

    Custom

统一：

-   calculate()
-   save()
-   load()
-   metadata()

------------------------------------------------------------------------

## 5.3 Analysis Layer

统一：

    AnalyzerBase

支持：

-   PCA
-   Kernel
-   Clustering
-   Similarity

------------------------------------------------------------------------

## 5.4 Workflow Layer

采用DAG：

    Task A

    ↓

    Task B

    ↓

    Task C

支持：

-   自动执行
-   状态管理
-   错误恢复

------------------------------------------------------------------------

# 6. 技术债务清理计划

## 高优先级

-   拆分 `pages/Analysis.tsx`（2093 行，单组件 1598 行）
-   建立 Benchmark 基线（1k / 10k / 100k 结构的 runtime 与 memory）

------------------------------------------------------------------------

## 中优先级

-   StructureFrame 统一结构对象
-   AnalysisObject：聚合已有的 `algorithm_version` / `cache_key` / `warnings_json`
-   测试目录分层（unit / integration）

------------------------------------------------------------------------

## 低优先级

-   大规模轨迹存储（lazy loading、chunk 读取、流式统计）
-   GPU 路径接入（引擎 0.3.x 起 CUDA 插件已随 wheel 发布并验收）
-   UI 细节与高级功能

------------------------------------------------------------------------

## 已清偿（原高/中优先级项，勿再列入）

-   硬编码参数、配置分散 → `config.py` 集中数据目录布局与协议版本
-   缺少异常处理 → `errors.py` 的 `AppError` + 稳定错误码 + `error_id`；
    后端已无 `except: pass`
-   日志系统 → `logging_setup.py`，各模块统一 `getLogger`
-   API 统一 → 引擎 schema 即契约（见 §2.2）
-   重复计算 → `descriptor_runs.cache_key` sha256 命中复用
-   全局变量 → 未发现成规模的问题

------------------------------------------------------------------------

# 7. 迁移路线

## Phase 1：稳定当前版本

目标：

保证功能不丢失。

完成：

-   测试补充
-   Bug修复
-   文档完善

------------------------------------------------------------------------

## Phase 2：核心重构

完成：

-   Data Layer
-   Descriptor API
-   Workflow Engine

------------------------------------------------------------------------

## Phase 3：插件化

完成：

-   Plugin Interface
-   Extension System

------------------------------------------------------------------------

## Phase 4：AI扩展

加入：

-   AI Assistant
-   Knowledge System
-   Automated Workflow

------------------------------------------------------------------------

# 8. 测试体系

建立：

## Unit Test

测试：

-   Descriptor
-   Data
-   Analysis

## Integration Test

测试：

完整Workflow。

## Regression Test

保证：

旧结果一致。

------------------------------------------------------------------------

# 9. 性能优化方向

包括：

-   Parallel Computing
-   GPU Acceleration
-   Cache
-   Distributed Execution

------------------------------------------------------------------------

# 10. 代码质量标准

要求：

-   Type Hint
-   Docstring
-   Static Analysis
-   Code Review

------------------------------------------------------------------------

# 11. 重构后的收益

## 开发

提高：

-   可维护性
-   扩展能力

## 科学

提高：

-   可复现性
-   可靠性

## 生态

支持：

-   Plugin
-   Community Development

------------------------------------------------------------------------

# 12. 最终目标

MDescriptorStudio v1.0目标架构：

    Scientific Core

    +

    Plugin Ecosystem

    +

    Workflow Engine

    +

    AI Extension

    +

    Data Infrastructure

形成：

    Professional Scientific Software Platform

------------------------------------------------------------------------

# 总结

Software Architecture Review and Refactoring Plan
是从当前代码走向未来平台架构的实施路线。

核心策略：

    Clean Code

    ↓

    Modular Architecture

    ↓

    Plugin System

    ↓

    AI Scientific Platform
