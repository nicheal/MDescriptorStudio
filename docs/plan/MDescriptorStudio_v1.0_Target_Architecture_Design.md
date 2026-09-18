# MDescriptorStudio v1.0 Target Architecture Design

## 1. 设计目标

目标：

将 MDescriptorStudio 从描述符分析工具升级为：

> 面向材料模拟、机器学习势函数开发和结构数据分析的一体化科学计算平台。

核心能力：

-   大规模结构数据管理
-   描述符计算
-   描述符空间分析
-   数据筛选
-   ML势训练
-   主动学习闭环
-   科学结果可追溯

------------------------------------------------------------------------

# 2. 总体架构

目标架构：

                        MDescriptorStudio

                             |
            ------------------------------------

            Application Layer

            React UI
            Visualization
            Workflow Control


                             |

            Tauri Desktop Layer

            IPC
            Process Management
            Packaging


                             |

            Backend Service Layer

            Job Manager
            Workflow Engine
            API Gateway


                             |

            Scientific Engine Layer

            Dataset Engine
            Descriptor Engine
            Analysis Engine
            ML Engine


                             |

            Storage Layer

            Database
            Cache
            Zarr/HDF5
            Provenance Database

------------------------------------------------------------------------

# 3. Python Backend目标结构

    mdescriptor_studio_backend/

    core/

        structure/
            frame.py
            trajectory.py

        descriptor/
            descriptor.py
            schema.py
            capability.py

        dataset/
            dataset.py
            provenance.py


    engine/

        descriptor_engine/

            soap.py
            ace.py
            acsf.py
            deepmd.py


        analysis_engine/

            pca.py
            kernel.py
            similarity.py
            clustering.py


        ml_engine/

            training.py
            validation.py
            active_learning.py


    workflow/

        jobs/

            scheduler.py
            state_machine.py


        pipeline/

            pipeline.py


    storage/

        database/

        cache/

        zarr/

注：描述符实现留在引擎内部，Studio 侧不再设 `engine/descriptor_engine/`
按算法分包；`ml_engine/`、`zarr/` 对应尚未落地的能力。

------------------------------------------------------------------------

# 4. 核心数据模型

## 4.1 StructureFrame

所有结构格式统一转换：

    POSCAR
    EXTXYZ
    LAMMPS dump
    DeepMD

            |

    StructureFrame

定义：

``` python
StructureFrame:

positions

atomic_numbers

cell

pbc

properties

metadata
```

------------------------------------------------------------------------

## 4.2 DatasetObject

描述数据集合：

``` python
DatasetObject:

id

name

source

fingerprint

frames

lineage

metadata
```

------------------------------------------------------------------------

## 4.3 DescriptorObject

版本、参数与血缘已由 `descriptor_runs` 承载（descriptor_version、engine_version、
parameters_json、scope、frame_index、output_dtype、device、cache_key），
矩阵规模在分析侧也有上限保护；本节只是把这些既有记录整理为显式对象：

``` python
DescriptorObject:

name

version

definition

parameters

values

statistics

provenance
```

------------------------------------------------------------------------

## 4.4 FeatureMatrix

用于连接：

Descriptor → Analysis → ML

``` python
FeatureMatrix:

data

sample_axis

feature_axis

normalization

metadata
```

------------------------------------------------------------------------

# 5. Descriptor Engine设计

## 统一接口

引擎的版本化 JSON schema（28 个描述符、170 个参数）已经是统一契约：
适配器动态枚举 `md.list_descriptors()`、逐项读取 `md.describe_descriptor(name)`、
以 `md.create_descriptor(cfg)` 构建；能力位由 schema 声明（`input.mixed_periodicity`、
`execution.devices`、`execution.cooperative_cancel`）。前端直接消费引擎 schema，
不再维护参数名映射表。新增描述符不需要改动 Studio 代码。

因此本节不再引入 `DescriptorBackend` 包装接口：
引擎 schema 已承担该角色，再包一层只会重复映射、增加维护成本。

------------------------------------------------------------------------

## Backend插件

描述符实现留在引擎内部，Studio 侧不存在按算法拆分的 backend 类
（`descriptor_backend/`、`SOAPBackend`、`ACEBackend` 等方案不再需要）。
引擎之外的扩展（第三方 ML 后端、可视化）见 Plugin System 文档。

------------------------------------------------------------------------

# 6. Analysis Engine设计

统一：

``` python
AnalysisObject
```

包含：

    input

    algorithm

    parameters

    version

    result

    statistics

    warnings

支持：

-   PCA
-   Kernel PCA
-   Similarity
-   Clustering
-   Feature importance

------------------------------------------------------------------------

# 7. Workflow Engine

任务生命周期已由 `services/job_service.py` 实现：

    QUEUED

     |

    RUNNING

     |

    COMPLETED

    or

    FAILED / CANCELLED

配套的健壮性机制同样已落地：`detach()` 与状态守卫的 UPDATE 阻止
CANCELLED→RUNNING 复活和重复 `job.finished`；构造期与关停期的
`_sweep_zombie_runs` 关闭崩溃遗留的 jobs/`descriptor_runs`/`analysis_runs` 行；
`_settle_linked_runs` 结算关联运行记录；`shutdown()` 协作式取消活动任务。

不新增 CANCEL_REQUESTED 中间态，也不新增
`cancel_requested_at` / `worker_id` / `error_trace` 列：
它们会重新打开当前设计已经关闭的竞态窗口。

负责：

-   长任务
-   并行计算
-   崩溃后回收（已实现）与检查点续算（未实现）
-   日志
-   进度

------------------------------------------------------------------------

# 8. Storage设计

## Database

保存：

-   用户项目
-   Dataset信息
-   Job状态
-   Provenance

## Zarr/HDF5

保存：

-   大规模descriptor
-   trajectory
-   feature matrix

## Cache

保存：

-   descriptor结果
-   analysis结果

------------------------------------------------------------------------

# 9. Provenance系统

追溯链已建立，下图与现有实现一致：

    Raw Dataset

        |

    Dataset Operation

        |

    Descriptor

        |

    Analysis

        |

    ML Model

已在记录：

-   软件版本（descriptor_version / engine_version）
-   参数（parameters_json）
-   数据范围（scope / frame_index / cache_key）
-   算法版本
-   资源占用（device / memory_peak_bytes）

视图与运行血缘由 `dataset_views` 表和 `analysis_runs.descriptor_run_id` 承担。
待补：ML 训练产物的模型版本登记。

------------------------------------------------------------------------

# 10. ML Workflow

完整流程：

    Dataset

    ↓

    Descriptor

    ↓

    Data Selection

    ↓

    Training

    ↓

    Validation

    ↓

    Uncertainty

    ↓

    New Data

支持：

-   MACE
-   NequIP
-   DeepMD
-   ACE potential

------------------------------------------------------------------------

# 11. Frontend架构

目标：

    src/

    app/

    providers/

    stores/

    dataset/

    descriptor/

    analysis/

    workflow/

    visualization/

状态拆分：

    datasetStore

    jobStore

    descriptorStore

    analysisStore

    modelStore

状态已按域拆分：`stores/workspace.ts`、`jobs.ts`、`analysisUi.ts`、`appUpdate.ts`，
不存在单一 workspace store 膨胀问题。剩余的是随功能落地补充 modelStore。

------------------------------------------------------------------------

# 12. Tauri边界

Rust负责：

-   生命周期
-   IPC
-   sidecar管理
-   安全

Python负责：

-   科学计算
-   数据处理
-   算法

React负责：

-   UI
-   可视化
-   用户交互

------------------------------------------------------------------------

# 13. 关键接口设计

## Dataset API

    load_dataset()

    get_frame()

    stream_frames()

    create_view()

------------------------------------------------------------------------

## Descriptor API

    list_descriptors()

    describe()

    compute()

    load_result()

------------------------------------------------------------------------

## Analysis API

    run_analysis()

    get_result()

    export()

------------------------------------------------------------------------

# 14. 性能目标

目标：

支持：

-   百万级MD frame
-   十万级结构descriptor
-   GB级结果文件

已具备：并行执行（引擎 num_threads / device 选项）、结果缓存、
矩阵规模上限保护（kernel 默认 400、硬上限 2000，并附采样受限警告）。

仍缺：

-   streaming统计
-   lazy loading
-   chunk storage

------------------------------------------------------------------------

# 15. 软件发表级要求

满足：

## 可复现

-   参数记录
-   数据版本
-   环境记录

## 可验证

-   regression test
-   benchmark

## 可扩展

-   plugin system
-   API documentation

------------------------------------------------------------------------

# 最终愿景

MDescriptorStudio成为：

    OVITO级结构分析能力

    +

    ASE级材料工作流

    +

    ML势开发平台

    +

    Descriptor空间探索工具

的综合科学计算软件。
