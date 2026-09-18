# MDescriptorStudio v1.0 Database Schema Design

## 1. 设计目标

数据库用于支撑：

-   数据集管理
-   结构轨迹管理
-   描述符计算
-   分析任务
-   ML模型管理
-   数据血缘追踪
-   Job调度

设计原则：

1.  科学数据可追溯
2.  支持大规模数据
3.  支持版本管理
4.  支持未来ML势工作流

------------------------------------------------------------------------

# 2. 数据库总体关系

    Dataset

       |

    Structure / Frame

       |

    Descriptor Run

       |

    Analysis Run

       |

    Model

同时：

    Dataset
       |
    Lineage Graph
       |
    Operation History

------------------------------------------------------------------------

# 3. datasets 表

保存原始数据集信息。

``` sql
datasets

id

name

source_path

format

fingerprint

number_of_frames

elements

periodicity

metadata

created_at

updated_at
```

字段说明：

-   fingerprint：数据版本校验
-   format：EXTXYZ/POSCAR/LAMMPS/DeepMD
-   metadata：扩展信息

------------------------------------------------------------------------

# 4. structures 表

保存结构级信息。

``` sql
structures

id

dataset_id

frame_index

energy

natoms

formula

cell

pbc

metadata
```

关系：

    dataset
     |
     |
    structures

------------------------------------------------------------------------

# 5. frames 表

用于大规模轨迹。

``` sql
frames

id

dataset_id

frame_index

offset

timestamp

properties
```

其中：

offset：

用于快速定位轨迹文件位置。

支持：

-   大规模MD
-   lazy loading

------------------------------------------------------------------------

# 6. descriptor_runs 表

描述符计算任务。

``` sql
descriptor_runs

id

dataset_id

descriptor_name

descriptor_version

parameters

engine_version

status

created_at
```

------------------------------------------------------------------------

# 7. descriptor_results 表

保存描述符结果。

``` sql
descriptor_results

id

run_id

storage_path

shape

dtype

statistics

checksum
```

不直接存储大型矩阵。

大型数据：

使用：

-   Zarr
-   HDF5

------------------------------------------------------------------------

# 8. analysis_runs 表

分析任务。

``` sql
analysis_runs

id

descriptor_run_id

analysis_type

parameters

algorithm_version

status

result_path

created_at
```

支持：

-   PCA
-   Kernel
-   clustering
-   similarity

------------------------------------------------------------------------

# 9. feature_selection 表

记录数据筛选。

``` sql
feature_selection

id

dataset_id

method

parameters

selected_indices

coverage

created_at
```

支持：

-   FPS
-   CUR
-   uncertainty sampling

------------------------------------------------------------------------

# 10. models 表

模型注册。

``` sql
models

id

name

architecture

version

training_dataset

descriptor

checkpoint

metrics

created_at
```

支持：

-   MACE
-   NequIP
-   DeepMD
-   ACE

------------------------------------------------------------------------

# 11. training_runs 表

训练记录。

``` sql
training_runs

id

model_id

dataset_id

hyperparameters

status

metrics

log_path

created_at
```

------------------------------------------------------------------------

# 12. provenance_graph 表

数据血缘。

``` sql
provenance_graph

id

source_type

source_id

target_type

target_id

operation

parameters

created_at
```

示例：

    raw_dataset

       |
    filter

       |

    training_dataset

       |

    model

------------------------------------------------------------------------

# 13. jobs 表

任务管理。

``` sql
jobs

id

type

status

priority

progress

worker_id

created_at

started_at

finished_at

error_message
```

状态：

    CREATED

    QUEUED

    RUNNING

    CANCEL_REQUESTED

    COMPLETED

    FAILED

    CANCELLED

------------------------------------------------------------------------

# 14. cache 表

计算缓存。

``` sql
cache

id

cache_key

object_type

object_id

checksum

created_at

last_access
```

支持：

-   descriptor缓存
-   analysis缓存

------------------------------------------------------------------------

# 15. 数据版本策略

所有科学对象必须记录：

    Dataset version

    +

    Descriptor version

    +

    Algorithm version

    +

    Software version

    +

    Environment version

保证结果可复现。

------------------------------------------------------------------------

# 16. 大数据存储策略

SQLite/PostgreSQL：

保存：

-   metadata
-   index
-   provenance

Zarr/HDF5：

保存：

-   trajectory
-   descriptor matrix
-   feature matrix

结构：

    database

     |

    metadata


    storage

     |

    zarr

     |

    large arrays

------------------------------------------------------------------------

# 17. 数据库迁移策略

采用：

migration table：

``` sql
schema_version

version

applied_at
```

禁止：

-   直接删除旧字段
-   破坏已有结果

采用：

add → migrate → deprecate

流程。

------------------------------------------------------------------------

# 18. 最终数据流

    Raw Data

    ↓

    Dataset Registry

    ↓

    StructureFrame

    ↓

    Descriptor Engine

    ↓

    Descriptor Storage

    ↓

    Analysis Engine

    ↓

    Selection

    ↓

    Training

    ↓

    Model Registry

    ↓

    Active Learning

    ↓

    New Dataset

------------------------------------------------------------------------

# 总结

该数据库设计目标：

支持从：

"描述符分析工具"

升级为：

"机器学习势函数研发平台"。

核心能力：

-   数据管理
-   描述符管理
-   分析管理
-   模型管理
-   主动学习闭环
-   科学结果追溯
