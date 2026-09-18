# MDescriptorStudio v1.0 Scientific Case Study

## 1. 案例目标

Scientific Case Study 用于展示 MDescriptorStudio
在真实材料模拟任务中的应用能力。

目标：

验证软件能够完成：

-   结构数据管理
-   描述符计算
-   描述符空间分析
-   代表性数据选择
-   机器学习势开发
-   主动学习闭环

------------------------------------------------------------------------

# 2. 案例总体流程

典型流程：

    Raw Simulation Data

            |

    Dataset Registration

            |

    Structure Validation

            |

    Descriptor Calculation

            |

    Descriptor Space Analysis

            |

    Representative Selection

            |

    ML Potential Training

            |

    Validation

            |

    Active Learning

            |

    Updated Dataset

------------------------------------------------------------------------

# 3. Case Study 1：晶体材料结构空间分析

## 研究目标

分析不同晶体结构在描述符空间中的分布。

示例体系：

-   Si
-   MoS2
-   氧化物材料

------------------------------------------------------------------------

## Step 1 数据导入

输入：

-   POSCAR
-   CIF
-   EXTXYZ

软件自动提取：

-   原子种类
-   晶格参数
-   原子坐标
-   周期边界

生成：

    DatasetObject

------------------------------------------------------------------------

## Step 2 Descriptor计算

选择：

SOAP Descriptor

参数：

    cutoff = 5 Å

    nmax = 8

    lmax = 6

    sigma = 0.5

输出：

    Atomic Descriptor Matrix

------------------------------------------------------------------------

## Step 3 PCA分析

目的：

观察结构空间分布。

输出：

-   PC1
-   PC2
-   explained variance

解释：

如果不同结构形成明显区域：

说明descriptor能够区分局域环境差异。

------------------------------------------------------------------------

# 4. Case Study 2：机器学习势训练数据选择

## 背景

MD模拟通常产生大量结构：

    10^5 - 10^6 frames

直接DFT标注成本高。

目标：

选择最具代表性的结构。

------------------------------------------------------------------------

# Step 1 Descriptor空间构建

流程：

    MD Trajectory

    ↓

    Descriptor

    ↓

    Feature Space

------------------------------------------------------------------------

# Step 2 Diversity Selection

采用：

## FPS

Farthest Point Sampling

算法：

    Select initial point

    ↓

    Calculate distance

    ↓

    Choose farthest structure

    ↓

    Repeat

------------------------------------------------------------------------

输出：

    Selected Training Dataset

------------------------------------------------------------------------

# Step 3 Coverage分析

使用：

Kernel Similarity

计算：

新结构与训练集相似度。

判断：

-   是否覆盖训练空间
-   是否存在外推区域

------------------------------------------------------------------------

# 5. Case Study 3：ACE/MACE势函数开发流程

## 数据流程

    DFT Dataset

    ↓

    Descriptor

    ↓

    Selection

    ↓

    Training

    ↓

    Validation

------------------------------------------------------------------------

## Descriptor配置

例如：

ACE：

    cutoff

    basis

    body order

------------------------------------------------------------------------

## Training

记录：

-   数据版本
-   Descriptor版本
-   模型参数
-   软件环境

生成：

    ModelObject

------------------------------------------------------------------------

# 6. 模型验证

## Energy误差

指标：

RMSE

MAE

------------------------------------------------------------------------

## Force误差

重点：

机器学习势关键指标。

分析：

    Predicted Force

    vs

    DFT Force

------------------------------------------------------------------------

## Structure Space验证

将：

训练集

测试集

投影到descriptor空间。

检查：

测试数据是否超出训练覆盖范围。

------------------------------------------------------------------------

# 7. Case Study 4：Active Learning闭环

## 目标

自动发现模型未知区域。

流程：

    Initial Dataset

    ↓

    Train Model

    ↓

    MD Simulation

    ↓

    Uncertainty Evaluation

    ↓

    Select Structures

    ↓

    DFT Label

    ↓

    Dataset Update

    ↓

    Retrain

------------------------------------------------------------------------

# 8. Uncertainty分析

支持：

## Kernel Distance

衡量：

结构与训练集距离。

## Ensemble Variance

多个模型预测差异。

输出：

``` json
{
structure_id":"frame1000",

uncertainty:0.85,

action:"select"
}
```

------------------------------------------------------------------------

# 9. Case Study 5：硬碳/复杂材料体系

## 应用场景

复杂材料：

-   非晶结构
-   缺陷体系
-   大规模MD轨迹

特点：

-   结构空间巨大
-   局域环境复杂
-   数据选择困难

------------------------------------------------------------------------

## MDescriptorStudio流程

    MD trajectory

    ↓

    Structure extraction

    ↓

    Descriptor calculation

    ↓

    Defect environment analysis

    ↓

    Representative selection

    ↓

    ML potential training

------------------------------------------------------------------------

# 10. 大规模性能案例

测试：

## Small

    100 structures

## Medium

    10000 structures

## Large

    100000 structures

记录：

-   计算时间
-   内存占用
-   存储大小
-   并行效率

------------------------------------------------------------------------

# 11. 软件可复现性

每个案例保存：

    Dataset fingerprint

    Descriptor version

    Parameter file

    Algorithm version

    Environment information

    Random seed

保证：

同样输入得到一致结果。

------------------------------------------------------------------------

# 12. CPC/JOSS论文展示建议

建议展示：

## Figure 1

软件架构：

Dataset → Descriptor → Analysis → ML Workflow

## Figure 2

Descriptor空间分析案例。

## Figure 3

数据选择效率比较。

## Figure 4

ML势训练精度。

## Figure 5

Active Learning闭环。

------------------------------------------------------------------------

# 13. 总结

Scientific Case Study证明：

MDescriptorStudio不仅能够进行描述符可视化分析，

还能够支持：

-   材料结构探索
-   机器学习势数据构建
-   模型训练
-   主动学习

最终形成：

    Structure Data

    ↓

    Scientific Representation

    ↓

    Machine Learning

    ↓

    Accelerated Simulation

的完整材料计算工作流。
