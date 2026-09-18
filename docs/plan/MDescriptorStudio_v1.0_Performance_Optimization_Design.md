# MDescriptorStudio v1.0 Performance Optimization Design

## 1. 设计目标

Performance Optimization Design 用于提升 MDescriptorStudio
在大规模材料模拟和机器学习势开发任务中的计算效率。

优化目标：

-   支持百万级结构数据
-   降低内存占用
-   提升Descriptor计算速度
-   提升GPU利用率
-   支持HPC并行计算
-   保持科学结果一致性

------------------------------------------------------------------------

# 2. 性能优化总体架构

    User Workflow

          |

    Workflow Engine

          |

    Parallel Scheduler

          |

    --------------------------------

    CPU Computing

    GPU Computing

    Distributed Computing

    Storage Optimization

    --------------------------------

          |

    Scientific Result

------------------------------------------------------------------------

# 3. 性能优化原则

## 3.1 算法优先

优化顺序：

    Algorithm

    ↓

    Data Structure

    ↓

    Parallelism

    ↓

    Hardware Acceleration

避免：

只通过增加硬件解决性能问题。

------------------------------------------------------------------------

## 3.2 可测量优化

每次优化必须记录：

-   原始性能
-   优化方案
-   优化后性能
-   测试数据规模
-   硬件环境

------------------------------------------------------------------------

# 4. Descriptor计算优化

## 4.1 批处理计算

问题：

逐结构计算：

    Frame1

    Frame2

    Frame3

导致：

大量Python调用开销。

优化：

采用batch：

    Frame Block

    ↓

    Descriptor Engine

    ↓

    Result Block

------------------------------------------------------------------------

## 4.2 多线程并行

CPU模式：

    Structure Dataset

            |

    Thread Pool

            |

    Descriptor Workers

参数：

-   worker数量
-   chunk大小

------------------------------------------------------------------------

## 4.3 GPU加速

支持：

-   CUDA Descriptor
-   GPU ML Backend

流程：

    CPU

    ↓

    Data Transfer

    ↓

    GPU Kernel

    ↓

    Descriptor Result

优化：

-   减少CPU-GPU复制
-   使用pinned memory
-   batch传输

------------------------------------------------------------------------

# 5. Descriptor缓存优化

避免重复计算——此机制已在 `services/descriptor_service.py` 落地：
sha256 `cache_key` 命中已 COMPLETED 的运行时直接复用现有结果，
同键 QUEUED/RUNNING 任务被合并，`descriptor_runs.cache_key` 列负责持久化；
`force` 参数可显式绕过缓存。

Cache Key（现状）：

    Dataset fingerprint

    +

    Descriptor name + engine_version

    +

    规范化参数

    +

    scope / frame_index / output_dtype

    +

    device / num_threads

流程（现状）：

    Request

    ↓

    Check Cache

    ↓

    Existing Result

    ↓

    Return

------------------------------------------------------------------------

# 6. Kernel Analysis优化

## 问题

Kernel矩阵：

\[ K=N`\times `{=tex}N \]

当：

N=100000

内存巨大。

现状：该风险已被上限拦住——`analysis/algorithms/kernel.py` 以 `max_samples`
（默认 400、硬上限 2000）经确定性的 `_bounded_indices` 截断样本，
并在结果里附带采样受限警告，因此不存在矩阵随 N² 失控的代码路径。

------------------------------------------------------------------------

## 优化方案

以下手段用于在保持上限语义的前提下提高可用样本量：

### Block Kernel

    K11 K12

    K21 K22

分块计算。

------------------------------------------------------------------------

### Approximate Kernel

支持：

-   Nyström
-   Random Features

------------------------------------------------------------------------

### Streaming Statistics

避免：

完整矩阵加载。

现状：完整矩阵加载已由样本上限避免；针对全量样本的流式统计仍待实现。

------------------------------------------------------------------------

# 7. PCA大规模优化

## 问题

传统PCA：

需要：

完整feature matrix。

------------------------------------------------------------------------

## 优化

支持：

## Incremental PCA

适合：

百万样本。

## Randomized PCA

降低：

SVD成本。

------------------------------------------------------------------------

# 8. 数据存储优化

## 8.1 Zarr

适合：

大规模数组。

优势：

-   chunk
-   parallel I/O
-   cloud compatible

------------------------------------------------------------------------

## 8.2 HDF5

适合：

结构化科学数据。

------------------------------------------------------------------------

## 8.3 Lazy Loading

原则：

不提前加载全部数据。

流程：

    Request Frame

    ↓

    Load Chunk

    ↓

    Compute

    ↓

    Release Memory

------------------------------------------------------------------------

# 9. 内存管理设计

已具备：

-   自动采样：各分析算法的 `max_samples` 上限配合确定性 `_bounded_indices`
-   受限内存的近邻参考搜索（`analysis/algorithms/_common.py`）
-   峰值内存计量（`descriptor_runs.memory_peak_bytes`）
-   科学警告层：采样受限、零方差/常量特征等警告（`analysis/algorithms/` 23 处）随每次结果返回

待补：

Memory Manager。

负责：

-   内存预测
-   block计算

所有降规模手段必须继续输出上述警告，不得静默采样。

例如：

    Matrix Request

    ↓

    Estimate Memory

    ↓

    Allow / Reduce / Reject

------------------------------------------------------------------------

# 10. CPU并行策略

支持：

## Thread Parallel

适合：

共享内存任务。

## Multiprocessing

适合：

Python计算。

## MPI

适合：

HPC节点。

------------------------------------------------------------------------

# 11. GPU资源管理

支持：

多GPU：

    GPU0

    GPU1

    GPU2

    GPU3

任务分配：

    Job Scheduler

    ↓

    GPU Queue

    ↓

    Worker

------------------------------------------------------------------------

记录：

-   GPU型号
-   显存
-   CUDA版本
-   利用率

------------------------------------------------------------------------

# 12. HPC扩展设计

支持：

-   Slurm
-   PBS

流程：

    Local GUI

    ↓

    Submit Job

    ↓

    Scheduler

    ↓

    Compute Node

    ↓

    Return Result

------------------------------------------------------------------------

# 13. Workflow性能优化

## Task并行

DAG：

    A

    |

    B   C

    |

    D

B和C可以同时运行。

------------------------------------------------------------------------

## Checkpoint

避免：

长任务失败重新计算。

------------------------------------------------------------------------

# 14. I/O优化

减少：

    Compute

    ↓

    Write

    ↓

    Read

频繁操作。

采用：

-   batch write
-   asynchronous I/O
-   compression

------------------------------------------------------------------------

# 15. Plugin性能规范

插件必须提供：

Benchmark：

包括：

-   单结构时间
-   批量时间
-   内存占用
-   GPU支持情况

------------------------------------------------------------------------

# 16. 性能测试体系

测试规模：

## Small

100 structures

## Medium

10000 structures

## Large

100000 structures

## Extreme

1000000 structures

------------------------------------------------------------------------

# 17. Benchmark指标

## 时间

    seconds/frame

## 内存

    GB

## 并行效率

    speedup

    efficiency

## GPU

    GPU utilization

------------------------------------------------------------------------

# 18. 性能回归测试

保存：

    benchmark/reference/

    descriptor.json

    kernel.json

    pca.json

防止：

优化导致性能下降。

------------------------------------------------------------------------

# 19. 典型优化路线

Phase 1：

-   Batch计算
-   Lazy loading

（Cache 已实现，不在本阶段范围内）

Phase 2：

-   Multi-thread
-   GPU acceleration

Phase 3：

-   MPI
-   HPC workflow

Phase 4：

-   Distributed computing

------------------------------------------------------------------------

# 20. 最终性能目标

支持：

    百万级结构

    ↓

    Descriptor计算

    ↓

    空间分析

    ↓

    数据选择

    ↓

    ML训练

同时保证：

-   数值一致
-   结果可复现
-   资源可控

------------------------------------------------------------------------

# 总结

Performance Optimization Design 使 MDescriptorStudio 从：

    桌面科学分析工具

升级为：

    高性能材料机器学习计算平台

核心优化方向：

-   算法优化
-   数据优化
-   并行优化
-   GPU优化
-   HPC扩展
