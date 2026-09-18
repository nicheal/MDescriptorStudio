# MDescriptorStudio v1.0 Testing and Benchmark Design

## 1. 设计目标

Testing and Benchmark体系用于保证 MDescriptorStudio：

-   数值正确
-   结果可复现
-   性能可扩展
-   科学计算可信

现状：

测试体系已建立并分层——`tests/` 下 32 个 `test_*.py` 覆盖数据集、描述符、
分析、Job、数据库迁移与安全；`tests/numerical/` 做数值验证（含 fuzz/hypothesis）；
`tests/regression/` 比对参考结果；全部由 `ci.yml` 执行。

因此本文档是"在既有套件上扩展"，未建成的部分是 Benchmark。

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

其中 Performance Benchmark 尚无任何实现，是本文档的主要开放项；
其余各层都已有对应用例，按需补充即可。

------------------------------------------------------------------------

# 3. Unit Test 单元测试

目标：

验证单个模块逻辑正确。

现状目录（已采用扁平布局，未再细分 `unit/`）：

    tests/

        test_*.py            # 后端各服务与算法

        numerical/           # 数值验证

        regression/          # 参考结果比对

        data/                # 小型参考数据

    frontend/src/**/*.test.ts(x)   # 组件、页面、store、IPC

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

已有：

    tests/data/

    extxyz_small.xyz

    deepmd_small/

    tests/regression/

    Si.xyz

    descriptor.npy

    expected.npz

扩展：

    Si_bulk

    MoS2

    water

    carbon

------------------------------------------------------------------------

## Reference结果

参考结果与比对流程已存在于 `tests/regression/`（`test_analysis_regression.py`），
新增体系按同一形式补充基线文件：

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

实际状态机已在 `services/job_service.py` 实现，且 `test_job_cancel.py`、
`test_job_scheduling.py`、`test_run_settlement.py`、`test_run_lifecycle.py`
已覆盖主干；下列用例按真实状态补全即可，不需要为不存在的中间态设计测试。

测试：

## 正常任务

    QUEUED

    RUNNING

    COMPLETED

------------------------------------------------------------------------

## 取消任务

    RUNNING

    ↓

    CANCELLED

取消无中间态：需断言 CANCELLED 不会被写回 RUNNING，
且 `job.finished` 不重复发出。

------------------------------------------------------------------------

## 异常任务

模拟：

-   backend crash（重启时 `_sweep_zombie_runs` 关闭遗留行）
-   timeout
-   memory error

------------------------------------------------------------------------

# 10. Benchmark设计

现状：仓库中不存在任何 benchmark 脚本或基线，本节起为真正的空白项。

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

`ci.yml` 已在 push/PR 上执行后端 pytest（含 numerical 与 regression）
和前端 type check、lint、单测；下列 Benchmark 与 Build Package 阶段仍待加入。

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

已达成：

科学模块：

    Regression Test 已入CI

关键算法：

    Numerical Validation 已由 tests/numerical 承担

待补（是补度量与门槛，不是补"从无到有的测试"）：

代码：

    Coverage：先接入统计读出真实现值，再设 >80% 门槛

性能：

    Benchmark 基线（第10–17节，目前完全缺失）

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
