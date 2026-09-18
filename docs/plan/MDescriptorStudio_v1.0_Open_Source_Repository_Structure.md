# MDescriptorStudio v1.0 Open Source Repository Structure

## 1. 设计目标

本仓库结构用于支持 MDescriptorStudio：

-   科学软件开发
-   开源协作
-   插件扩展
-   持续集成
-   文档维护
-   软件发布

目标：

建立符合专业科学软件项目规范的 GitHub Repository。

------------------------------------------------------------------------

# 2. 总体目录结构

    MDescriptorStudio/

    ├── backend/

    ├── frontend/

    ├── plugins/

    ├── core/

    ├── workflows/

    ├── storage/

    ├── tests/

    ├── benchmarks/

    ├── examples/

    ├── docs/

    ├── docker/

    ├── scripts/

    ├── .github/

    ├── LICENSE

    ├── README.md

    ├── CONTRIBUTING.md

    ├── CODE_OF_CONDUCT.md

    └── CHANGELOG.md

------------------------------------------------------------------------

# 3. Backend目录

负责科学计算核心。

    backend/

    ├── api/

    ├── core/

    ├── engine/

    ├── workflow/

    ├── storage/

    ├── services/

    └── tests/

------------------------------------------------------------------------

## core

核心科学对象：

    core/

    ├── structure/

    │   ├── frame.py

    │   └── trajectory.py


    ├── dataset/

    │   └── dataset.py


    └── descriptor/

        └── descriptor.py

包含：

-   StructureFrame
-   DatasetObject
-   DescriptorObject
-   FeatureMatrix

------------------------------------------------------------------------

## engine

科学算法实现：

    engine/

    ├── descriptor_engine/

    ├── analysis_engine/

    └── ml_engine/

------------------------------------------------------------------------

# 4. Frontend目录

React界面。

    frontend/

    ├── src/

    │
    ├── components/

    ├── pages/

    ├── stores/

    ├── visualization/

    └── api/

职责：

-   用户交互
-   数据展示
-   Workflow控制

------------------------------------------------------------------------

# 5. Plugin目录

第三方扩展。

    plugins/

    ├── descriptor/

    ├── analysis/

    ├── dataset/

    ├── ml/

    └── visualization/

------------------------------------------------------------------------

示例：

    plugins/descriptor/

    ├── soap/

    ├── ace/

    └── custom_descriptor/

每个插件包含：

    plugin.json

    source code

    tests

    README.md

------------------------------------------------------------------------

# 6. Workflow目录

科学工作流定义。

    workflows/

    ├── templates/

    ├── examples/

    └── schemas/

保存：

-   Pipeline模板
-   DAG定义
-   参数配置

------------------------------------------------------------------------

# 7. Storage目录

数据存储接口。

    storage/

    ├── database/

    ├── cache/

    ├── zarr/

    └── hdf5/

保存：

-   元数据
-   descriptor矩阵
-   trajectory
-   analysis结果

------------------------------------------------------------------------

# 8. Tests目录

测试体系。

    tests/

    ├── unit/

    ├── integration/

    ├── scientific/

    ├── regression/

    └── workflow/

------------------------------------------------------------------------

## unit

测试：

-   类
-   函数
-   API

## scientific

测试：

-   descriptor数值
-   PCA结果
-   kernel结果

## regression

保存：

-   reference数据
-   benchmark结果

------------------------------------------------------------------------

# 9. Benchmarks目录

性能测试。

    benchmarks/

    ├── descriptor/

    ├── analysis/

    ├── storage/

    ├── scaling/

    └── reports/

记录：

-   runtime
-   memory
-   CPU scaling
-   GPU scaling

------------------------------------------------------------------------

# 10. Examples目录

用户示例。

    examples/

    ├── dataset_import/

    ├── descriptor_analysis/

    ├── kernel_analysis/

    ├── ml_training/

    └── active_learning/

------------------------------------------------------------------------

# 11. Docs目录

文档中心。

    docs/

    ├── architecture/

    ├── developer/

    ├── user/

    ├── api/

    ├── tutorials/

    └── publication/

------------------------------------------------------------------------

# 12. Docker目录

环境部署。

    docker/

    ├── Dockerfile

    ├── docker-compose.yml

    └── cuda/

支持：

-   CI环境
-   GPU环境
-   HPC测试

------------------------------------------------------------------------

# 13. Scripts目录

自动化脚本。

    scripts/

    ├── install.sh

    ├── build.sh

    ├── test.sh

    └── benchmark.sh

------------------------------------------------------------------------

# 14. GitHub配置

目录：

    .github/

    ├── workflows/

    ├── ISSUE_TEMPLATE/

    └── PULL_REQUEST_TEMPLATE.md

------------------------------------------------------------------------

## GitHub Actions

自动执行：

    push

    ↓

    test

    ↓

    benchmark

    ↓

    build

------------------------------------------------------------------------

# 15. Issue模板

包含：

## Bug Report

字段：

-   软件版本
-   系统
-   环境
-   错误日志

## Feature Request

字段：

-   功能描述
-   使用场景
-   设计建议

------------------------------------------------------------------------

# 16. Pull Request规范

PR必须包含：

-   修改说明
-   测试结果
-   Benchmark结果
-   文档更新

------------------------------------------------------------------------

# 17. README设计

README包含：

## 项目介绍

说明：

MDescriptorStudio定位。

## Features

列出：

-   Descriptor analysis
-   ML workflow
-   Plugin system

## Installation

提供：

-   Binary
-   Source

## Quick Start

示例：

    Import Dataset

    Calculate Descriptor

    Analyze Space

------------------------------------------------------------------------

# 18. LICENSE建议

推荐：

## MIT

优点：

-   开放
-   简洁

或者：

## Apache-2.0

优势：

-   企业友好
-   专利保护

------------------------------------------------------------------------

# 19. CONTRIBUTING.md

包含：

-   开发环境
-   Coding style
-   Testing
-   PR流程

------------------------------------------------------------------------

# 20. CHANGELOG

格式：

    v1.0.0

    Added:

    - Plugin system
    - Workflow engine


    Fixed:

    - Job manager issues

------------------------------------------------------------------------

# 21. Release目录

发布：

    releases/

    ├── binaries/

    ├── source/

    └── checksums/

------------------------------------------------------------------------

# 22. 开源协作流程

推荐：

    Issue

    ↓

    Discussion

    ↓

    Feature Branch

    ↓

    Pull Request

    ↓

    CI

    ↓

    Review

    ↓

    Merge

    ↓

    Release

------------------------------------------------------------------------

# 23. 最终GitHub结构

    MDescriptorStudio

    |

    ├── Scientific Core

    ├── Plugin Ecosystem

    ├── Workflow Engine

    ├── Documentation

    ├── Testing

    ├── Benchmark

    └── Release System

------------------------------------------------------------------------

# 总结

该仓库结构支持：

-   长期维护
-   多开发者协作
-   科学软件验证
-   插件生态
-   开源发布

使 MDescriptorStudio 从个人项目升级为：

> 可持续发展的开源科学计算平台。
