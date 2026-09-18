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

替代简单numpy矩阵：

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

``` python
class DescriptorBackend:

    name()

    schema()

    capabilities()

    compute()
```

------------------------------------------------------------------------

## Backend插件

    descriptor_backend/

    SOAPBackend

    ACEBackend

    ACSFBackend

    MACEBackend

    DeepMDBackend

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

任务生命周期：

    CREATED

     |

    QUEUED

     |

    RUNNING

     |

    CANCEL_REQUESTED

     |

    COMPLETED

    or

    CANCELLED

负责：

-   长任务
-   并行计算
-   恢复
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

所有结果必须可追溯：

    Raw Dataset

        |

    Dataset Operation

        |

    Descriptor

        |

    Analysis

        |

    ML Model

保存：

-   软件版本
-   参数
-   数据hash
-   算法版本

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

避免单一workspace store膨胀。

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

要求：

-   streaming
-   lazy loading
-   chunk storage
-   parallel execution

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
