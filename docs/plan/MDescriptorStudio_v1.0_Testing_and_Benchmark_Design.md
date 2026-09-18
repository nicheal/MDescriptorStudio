# MDescriptorStudio v1.0 Testing and Benchmark Design

## 1. 设计目标

Testing and Benchmark体系用于保证 MDescriptorStudio：

-   数值正确
-   结果可复现
-   性能可扩展
-   科学计算可信

目标：

达到：

-   CPC（Computer Physics Communications）
-   JOSS（Journal of Open Source Software）

级科学软件质量要求。

------------------------------------------------------------------------

# 2. 测试体系总体架构

    Testing Framework

            |

    --------------------------------

    Unit Test

    Integration Test

    Scientific Regression Test

    Performance Benchmark

    Compatibility Test

    Workflow Test

    --------------------------------

------------------------------------------------------------------------

# 3. Unit Test 单元测试

目标：

验证单个模块逻辑正确。

目录：

    tests/

    unit/

        core/

        descriptor/

        analysis/

        dataset/

        workflow/

------------------------------------------------------------------------

# 4. Core模块测试

测试：

## StructureFrame

验证：

-   坐标读取
-   原子编号转换
-   cell处理
-   PBC处理

示例：

输入：

    Si crystal

检查：

    atomic_numbers

    positions

    cell

    pbc

------------------------------------------------------------------------

## DatasetObject

测试：

-   fingerprint
-   metadata
-   lineage

------------------------------------------------------------------------

# 5. Descriptor测试

## 5.1 SOAP测试

验证：

输入：

固定结构

输出：

descriptor shape

数值范围

一致性

测试：

    same input

    ↓

    same descriptor

------------------------------------------------------------------------

## 5.2 ACE测试

验证：

-   basis参数
-   cutoff
-   body order
-   symmetry

------------------------------------------------------------------------

## 5.3 Descriptor API测试

所有插件必须通过：

    schema validation

    capability validation

    compute validation

------------------------------------------------------------------------

# 6. Analysis科学测试

## PCA测试

验证：

-   covariance计算
-   eigenvalue排序
-   explained variance

保存：

    pca_reference.json

------------------------------------------------------------------------

## Kernel测试

验证：

-   kernel矩阵
-   centering
-   eigen spectrum

保存：

    kernel_reference.json

------------------------------------------------------------------------

## Clustering测试

验证：

-   cluster数量
-   label稳定性

------------------------------------------------------------------------

# 7. Scientific Regression Test

这是科学软件最重要部分。

目标：

防止代码修改导致：

"程序运行正常，但科学结果变化"。

------------------------------------------------------------------------

## Reference Dataset

建立：

    tests/data/

    Si_bulk

    MoS2

    water

    carbon

------------------------------------------------------------------------

## Reference结果

保存：

    reference/

    descriptor.npy

    pca.json

    kernel.json

    cluster.json

------------------------------------------------------------------------

比较：

    new result

    vs

    reference result

允许误差：

例如：

    1e-8

或者：

    relative error < 1e-5

------------------------------------------------------------------------

# 8. Workflow测试

测试完整流程：

    Dataset

    ↓

    Descriptor

    ↓

    Analysis

    ↓

    Selection

    ↓

    Export

检查：

-   状态转换
-   错误恢复
-   checkpoint

------------------------------------------------------------------------

# 9. Job系统测试

测试：

## 正常任务

    CREATED

    QUEUED

    RUNNING

    SUCCESS

------------------------------------------------------------------------

## 取消任务

    RUNNING

    ↓

    CANCEL_REQUESTED

    ↓

    CANCELLED

------------------------------------------------------------------------

## 异常任务

模拟：

-   backend crash
-   timeout
-   memory error

------------------------------------------------------------------------

# 10. Benchmark设计

目标：

评价：

-   速度
-   内存
-   扩展性

------------------------------------------------------------------------

# 11. 数据规模Benchmark

建立：

## Small

    100 structures

## Medium

    10000 structures

## Large

    100000 structures

------------------------------------------------------------------------

测试：

-   导入时间
-   fingerprint时间
-   descriptor时间
-   analysis时间

------------------------------------------------------------------------

# 12. Descriptor Benchmark

比较：

支持：

-   SOAP
-   ACSF
-   ACE

指标：

    time/frame

    memory

    feature dimension

    storage size

------------------------------------------------------------------------

示例：

  Descriptor   Time   Dimension   Memory
  ------------ ------ ----------- --------
  SOAP                            
  ACE                             

------------------------------------------------------------------------

# 13. Analysis Benchmark

测试：

## PCA

指标：

-   CPU时间
-   内存

## Kernel

指标：

-   matrix size
-   eigen decomposition time

## Clustering

指标：

-   scaling

------------------------------------------------------------------------

# 14. 大规模数据Benchmark

目标：

测试：

百万级frame。

测试：

    Trajectory

    ↓

    Index

    ↓

    Random access

    ↓

    Streaming analysis

指标：

-   frame/s
-   memory usage

------------------------------------------------------------------------

# 15. GPU Benchmark

支持：

GPU descriptor。

测试：

    CPU

    vs

    GPU

指标：

-   speedup
-   GPU memory
-   utilization

------------------------------------------------------------------------

# 16. 并行Benchmark

测试：

CPU：

    1 core

    4 cores

    16 cores

    64 cores

分析：

strong scaling

weak scaling

------------------------------------------------------------------------

# 17. 第三方软件对比

建议比较：

## Descriptor

-   DScribe
-   ACE implementations

## Structure

-   ASE

## ML Potential

-   MACE
-   DeepMD

------------------------------------------------------------------------

比较：

-   API易用性
-   性能
-   可扩展性

------------------------------------------------------------------------

# 18. Continuous Integration

CI流程：

    Git Push

    ↓

    Unit Test

    ↓

    Regression Test

    ↓

    Benchmark Smoke Test

    ↓

    Build Package

------------------------------------------------------------------------

# 19. 测试环境矩阵

支持：

## OS

-   Linux
-   Windows

## Python

-   3.10
-   3.11
-   3.12

## Hardware

-   CPU
-   NVIDIA GPU

------------------------------------------------------------------------

# 20. 软件质量指标

目标：

代码：

    Coverage >80%

科学模块：

    Regression Test 100%

关键算法：

    Numerical Validation

------------------------------------------------------------------------

# 21. CPC/JOSS发表建议展示

论文中展示：

## 软件架构

展示：

模块关系。

## Benchmark

展示：

数据规模增长。

## 科学案例

展示：

真实材料体系：

-   Descriptor空间分析
-   数据选择
-   ML势训练

------------------------------------------------------------------------

# 总结

Testing and Benchmark体系保证：

    代码可靠性

    +

    数值正确性

    +

    性能可扩展性

    +

    科学可复现性

使 MDescriptorStudio 达到专业科学软件标准。
