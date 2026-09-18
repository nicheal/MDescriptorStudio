# MDescriptorStudio v1.0 Scientific Workflow Design

## 1. 设计目标

Scientific Workflow 是 MDescriptorStudio
从工具型软件升级为科学计算平台的核心。

目标：

实现：

    Dataset

    ↓

    Structure Validation

    ↓

    Descriptor Calculation

    ↓

    Descriptor Analysis

    ↓

    Data Selection

    ↓

    ML Training

    ↓

    Model Validation

    ↓

    Active Learning

    ↓

    New Dataset

形成完整材料模拟与机器学习势研发闭环。

------------------------------------------------------------------------

# 2. Workflow总体架构

                    Workflow Engine


                          |

                  Pipeline DAG Manager


                          |

     ------------------------------------------------

     Dataset Task

     Descriptor Task

     Analysis Task

     Selection Task

     Training Task

     Validation Task

     Active Learning Task

     ------------------------------------------------


                          |

                     Job Scheduler

                          |

                  Compute Resources

              CPU / GPU / Remote HPC

------------------------------------------------------------------------

# 3. Workflow核心组件

## 3.1 Workflow Engine

负责：

-   创建流程
-   管理任务依赖
-   调度任务
-   保存状态
-   失败恢复

目录：

    workflow/

    engine.py

    pipeline.py

    scheduler.py

    executor.py

------------------------------------------------------------------------

# 4. Pipeline DAG设计

工作流采用有向无环图：

    Dataset Import

          |

    Descriptor Calculation

          |

    PCA Analysis

          |

    Selection

          |

    Training

          |

    Validation

每个节点：

    Task Node

    输入

    参数

    输出

    状态

    日志

------------------------------------------------------------------------

# 5. Task对象设计

统一任务接口：

``` python
class Task:

    name

    inputs

    parameters

    outputs


    def execute():
        pass
```

------------------------------------------------------------------------

# 6. Dataset Workflow

流程：

    Import Dataset

            |

    Detect Format

            |

    Generate Fingerprint

            |

    Build Structure Index

            |

    Validate Data

检查：

-   原子类型
-   坐标
-   cell
-   PBC
-   能量
-   力
-   应力

------------------------------------------------------------------------

# 7. Descriptor Workflow

流程：

    StructureFrame

          |

    Descriptor Plugin

          |

    DescriptorObject

          |

    Storage

记录：

-   descriptor名称
-   版本
-   参数
-   软件环境
-   数据版本

------------------------------------------------------------------------

# 8. Analysis Workflow

支持：

## PCA

输入：

Descriptor Matrix

输出：

-   embedding
-   variance ratio

------------------------------------------------------------------------

## Kernel Analysis

输出：

-   similarity matrix
-   eigen spectrum
-   effective rank

------------------------------------------------------------------------

## Clustering

输出：

-   cluster label
-   representative structures

------------------------------------------------------------------------

# 9. Data Selection Workflow

目标：

从大量结构中选择训练数据。

流程：

    Descriptor Space

          |

    Diversity Calculation

          |

    Selection Algorithm

          |

    Training Dataset

支持：

-   FPS
-   CUR
-   D-optimal
-   uncertainty sampling

------------------------------------------------------------------------

# 10. ML Training Workflow

流程：

    Dataset

    ↓

    Descriptor

    ↓

    Model Configuration

    ↓

    Training

    ↓

    Evaluation

    ↓

    Model Registry

支持：

-   MACE
-   NequIP
-   DeepMD
-   ACE

------------------------------------------------------------------------

# 11. Validation Workflow

评价：

## Energy

RMSE

MAE

## Force

RMSE

MAE

## Stress

Error distribution

## Structure Space

分析：

训练集覆盖范围。

------------------------------------------------------------------------

# 12. Active Learning Workflow

完整闭环：

    Initial Dataset

           |

    Train Model

           |

    MD Simulation

           |

    Uncertainty Estimation

           |

    Select New Structures

           |

    DFT Labeling

           |

    Dataset Update

           |

    Retrain

------------------------------------------------------------------------

# 13. Uncertainty接口设计

支持：

-   ensemble variance
-   Bayesian uncertainty
-   kernel distance
-   extrapolation grade

输出：

``` json
{
"structure_id":"frame100",

"uncertainty":0.85,

"recommendation":"select"
}
```

------------------------------------------------------------------------

# 14. Job调度设计

任务状态：

    CREATED

    ↓

    QUEUED

    ↓

    RUNNING

    ↓

    SUCCESS


    or


    FAILED

    CANCELLED

保存：

-   stdout
-   stderr
-   runtime
-   resource usage

------------------------------------------------------------------------

# 15. Checkpoint设计

长任务必须支持：

-   保存中间状态
-   断点恢复

例如：

Descriptor计算：

    100000 frames

    processed:

    50000

    checkpoint:

    frame50000

------------------------------------------------------------------------

# 16. 多资源调度

支持：

## CPU

-   multiprocessing
-   MPI

## GPU

-   CUDA
-   GPU queue

## HPC

支持：

-   Slurm
-   PBS

------------------------------------------------------------------------

# 17. Workflow Provenance

每个结果记录：

    Workflow ID

    ↓

    Task Graph

    ↓

    Input Dataset

    ↓

    Parameters

    ↓

    Software Version

    ↓

    Output

保证：

科学结果可复现。

------------------------------------------------------------------------

# 18. 自动重算机制

当：

Dataset fingerprint变化

或者：

Descriptor版本变化

自动：

    Invalidate Result

            |

    Recompute

避免使用过期结果。

------------------------------------------------------------------------

# 19. Workflow API

示例：

创建流程：

    workflow.create

提交：

    workflow.submit

查询：

    workflow.status

取消：

    workflow.cancel

导出：

    workflow.export

------------------------------------------------------------------------

# 20. 前端展示

Workflow UI：

    Dataset

      ✓

    Descriptor

      ✓

    Analysis

      Running

    Training

      Waiting

    Validation

      Pending

显示：

-   状态
-   时间
-   资源
-   日志
-   错误

------------------------------------------------------------------------

# 21. 最终科学工作流平台

最终：

                     MDescriptorStudio


    Dataset Management

            |

    Descriptor Engine

            |

    Scientific Analysis

            |

    Data Selection

            |

    ML Potential Training

            |

    Validation

            |

    Active Learning

------------------------------------------------------------------------

# 总结

Scientific Workflow Design 使 MDescriptorStudio 从：

    描述符分析软件

升级为：

    材料机器学习势研发平台

核心能力：

-   自动化计算流程
-   任务管理
-   数据追踪
-   模型训练
-   主动学习闭环
-   科学结果复现
