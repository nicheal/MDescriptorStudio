# MDescriptorStudio v1.0 Repository Reorganization Plan

## 1. 文档目标

本文档用于指导 GitHub 仓库从当前结构迁移到 MDescriptorStudio v1.0
标准工程结构。

目标：

    Current Repository

    ↓

    Clean Repository Structure

    ↓

    Professional Scientific Software Package

解决：

-   文件组织混乱
-   模块职责不清
-   import依赖复杂
-   测试分散
-   文档缺失

------------------------------------------------------------------------

# 2. 重组原则

## 原则1：功能分层

采用：

    Application

    ↓

    Service

    ↓

    Core

    ↓

    Scientific Engine

------------------------------------------------------------------------

## 原则2：核心代码独立

核心计算不能依赖：

-   GUI
-   Notebook
-   Demo

------------------------------------------------------------------------

## 原则3：测试与代码同步

结构：

    src/

    tests/

保持对应。

------------------------------------------------------------------------

# 3. 新仓库结构设计

目标：

    MDescriptorStudio/

    ├── src/

    │   └── mdescriptorstudio/

    │       ├── core/

    │       ├── data/

    │       ├── descriptors/

    │       ├── analysis/

    │       ├── workflow/

    │       ├── plugins/

    │       ├── services/

    │       └── api/


    ├── tests/

    │   ├── unit/

    │   ├── integration/

    │   └── regression/


    ├── docs/

    │   ├── user/

    │   ├── developer/

    │   ├── api/

    │   └── tutorials/


    ├── examples/

    ├── scripts/

    ├── benchmarks/

    ├── configs/

    ├── datasets/


    ├── pyproject.toml

    ├── README.md

    └── LICENSE

------------------------------------------------------------------------

# 4. 当前代码迁移分类

## Core模块

迁移：

-   基础数据结构
-   通用工具
-   数学函数

目标：

    src/mdescriptorstudio/core/

------------------------------------------------------------------------

## Data模块

迁移：

-   数据读取
-   数据转换
-   数据格式

目标：

    src/mdescriptorstudio/data/

------------------------------------------------------------------------

## Descriptor模块

迁移：

-   SOAP
-   ACE
-   ACSF
-   Custom Descriptor

目标：

    src/mdescriptorstudio/descriptors/

------------------------------------------------------------------------

## Analysis模块

迁移：

-   PCA
-   Kernel
-   Similarity
-   Clustering

目标：

    src/mdescriptorstudio/analysis/

------------------------------------------------------------------------

## Workflow模块

迁移：

-   Pipeline
-   Task
-   Execution

目标：

    src/mdescriptorstudio/workflow/

------------------------------------------------------------------------

# 5. Import重构策略

旧：

``` python
from xxx import function
```

问题：

-   路径依赖
-   难维护

新：

``` python
from mdescriptorstudio.core import xxx
```

------------------------------------------------------------------------

# 6. 删除冗余代码策略

## 第一类：重复实现

处理：

-   合并重复函数
-   保留统一入口

------------------------------------------------------------------------

## 第二类：废弃实验代码

迁移：

    archive/

不进入核心包。

------------------------------------------------------------------------

## 第三类：临时代码

删除：

-   debug脚本
-   临时Notebook
-   测试文件

------------------------------------------------------------------------

# 7. Notebook管理

Notebook分为：

## Tutorial

公开教学。

位置：

    examples/tutorials/

## Research

科研分析。

位置：

    examples/research/

## Temporary

归档：

    archive/notebooks/

------------------------------------------------------------------------

# 8. Package发布结构

支持：

    pip install mdescriptorstudio

配置：

    pyproject.toml

包含：

-   dependencies
-   version
-   entry points

------------------------------------------------------------------------

# 9. Plugin目录设计

结构：

    plugins/

    ├── descriptor/

    ├── analysis/

    ├── simulation/

    └── visualization/

每个插件：

    plugin_name/

    ├── __init__.py

    ├── plugin.yaml

    ├── module.py

    └── tests/

------------------------------------------------------------------------

# 10. 文档体系

目录：

    docs/

    ├── getting_started

    ├── user_manual

    ├── developer

    ├── api_reference

    ├── scientific_cases

    └── architecture

------------------------------------------------------------------------

# 11. CI/CD结构

自动检查：

-   pytest
-   lint
-   type check
-   build package

流程：

    Commit

    ↓

    CI

    ↓

    Test

    ↓

    Build

    ↓

    Release

------------------------------------------------------------------------

# 12. Git迁移步骤

## Step 1

创建：

    refactor/repository-layout

------------------------------------------------------------------------

## Step 2

迁移：

-   src结构
-   tests结构
-   docs结构

------------------------------------------------------------------------

## Step 3

修复：

-   imports
-   package metadata

------------------------------------------------------------------------

## Step 4

运行：

-   Unit Test
-   Integration Test

------------------------------------------------------------------------

## Step 5

合并：

    develop

    ↓

    main

------------------------------------------------------------------------

# 13. 验收标准

完成后：

## 软件

-   可以安装
-   可以导入
-   可以运行示例

## 科学

-   Descriptor结果一致
-   Analysis结果一致

## 工程

-   测试通过
-   文档完整

------------------------------------------------------------------------

# 14. 最终仓库目标

    MDescriptorStudio

    =

    Scientific Core

    +

    Clean Architecture

    +

    Plugin Ecosystem

    +

    Documentation

    +

    Testing

------------------------------------------------------------------------

# 总结

Repository Reorganization Plan 提供 GitHub 仓库结构重构方案。

核心目标：

    Messy Codebase

    ↓

    Professional Scientific Software Repository

为后续：

-   AI扩展
-   Plugin生态
-   开源发布
-   软件论文

提供稳定基础。
