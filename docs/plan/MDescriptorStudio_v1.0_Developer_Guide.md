# MDescriptorStudio v1.0 Developer Guide

## 1. 文档目标

本开发者指南用于指导：

-   MDescriptorStudio源码开发
-   模块扩展
-   Plugin开发
-   Backend维护
-   Frontend开发
-   测试与发布

目标：

建立可持续维护的科学软件开发流程。

------------------------------------------------------------------------

# 2. 开发环境

## 2.1 推荐环境

操作系统：

-   Ubuntu 22.04+
-   Windows 11

Python：

    Python >=3.10

Node:

    Node.js >=20

Rust:

    Rust stable

------------------------------------------------------------------------

# 3. 项目目录结构

目标结构：

    MDescriptorStudio/

    frontend/

        src/

        components/

        stores/


    src-tauri/

        src/

        commands/


    backend/

        core/

        engine/

        workflow/

        storage/


    plugins/

        descriptor/

        analysis/

        dataset/


    tests/

    docs/

------------------------------------------------------------------------

# 4. 开发流程

标准流程：

    Create Branch

    ↓

    Implement Feature

    ↓

    Add Test

    ↓

    Run Benchmark

    ↓

    Code Review

    ↓

    Merge

------------------------------------------------------------------------

# 5. Backend开发

## 5.1 Backend职责

Python Backend负责：

-   科学计算
-   数据处理
-   Descriptor计算
-   Analysis算法
-   Workflow执行

不负责：

-   UI
-   用户交互

------------------------------------------------------------------------

# 6. 新增科学模块

推荐结构：

    engine/

    new_module/

        __init__.py

        core.py

        schema.py

        tests/

要求：

包含：

-   API接口
-   参数验证
-   单元测试
-   文档

------------------------------------------------------------------------

# 7. Descriptor插件开发

## 7.1 创建插件

目录：

    plugins/

    descriptor/

    my_descriptor/

        plugin.json

        descriptor.py

        tests/

------------------------------------------------------------------------

## 7.2 plugin.json

示例：

``` json
{
"name":"My Descriptor",

"id":"descriptor.my",

"type":"descriptor",

"version":"1.0"
}
```

------------------------------------------------------------------------

## 7.3 实现接口

``` python
class DescriptorPlugin:


    def schema(self):
        pass


    def capabilities(self):
        pass


    def compute(self, structure):
        pass
```

------------------------------------------------------------------------

# 8. Dataset Adapter开发

用于支持新数据格式。

目录：

    plugins/dataset/

    format_name/

        adapter.py

接口：

``` python
class DatasetAdapter:


    def detect(self,path):
        pass


    def read_frame(self,index):
        pass


    def metadata(self):
        pass
```

------------------------------------------------------------------------

支持格式：

-   EXTXYZ
-   POSCAR
-   CIF
-   LAMMPS
-   DeepMD

------------------------------------------------------------------------

# 9. Analysis模块开发

接口：

``` python
class AnalysisPlugin:


    def run(self,data,parameters):
        pass


    def result_schema(self):
        pass
```

------------------------------------------------------------------------

开发要求：

必须保存：

-   输入
-   参数
-   算法版本
-   输出

------------------------------------------------------------------------

# 10. Frontend开发

目录：

    frontend/src/

    app/

    components/

    stores/

    pages/

    visualization/

------------------------------------------------------------------------

原则：

组件：

负责UI。

Store：

负责状态。

Backend：

负责计算。

------------------------------------------------------------------------

# 11. Tauri IPC开发

通信：

    React

     |

    Tauri IPC

     |

    Python Backend

------------------------------------------------------------------------

Request：

``` json
{
"id":"001",

"method":"descriptor.compute",

"params":{}
}
```

------------------------------------------------------------------------

Response：

``` json
{
"id":"001",

"status":"ok",

"result":{}
}
```

------------------------------------------------------------------------

# 12. 新增API流程

步骤：

1.  Backend实现method

2.  添加schema

3.  注册RPC

4.  Frontend调用

5.  添加测试

------------------------------------------------------------------------

# 13. 数据库开发

修改数据库：

必须：

新增migration。

流程：

    Create Migration

    ↓

    Test Upgrade

    ↓

    Test Rollback

    ↓

    Merge

禁止：

直接修改已有字段。

------------------------------------------------------------------------

# 14. 测试开发

运行：

    pytest tests/

测试类型：

    unit/

    integration/

    scientific/

    regression/

    benchmark/

------------------------------------------------------------------------

# 15. 科学回归测试

新增算法必须提供：

reference数据。

例如：

    tests/reference/

    descriptor.npy

    pca.json

    kernel.json

保证：

代码变化不会改变科学结果。

------------------------------------------------------------------------

# 16. Benchmark开发

新增功能必须测试：

-   时间
-   内存
-   数据规模

记录：

    benchmark/

    results/

    reports/

------------------------------------------------------------------------

# 17. Debug方法

## Backend

日志：

    backend/logs/

检查：

-   RPC
-   Job状态
-   数据加载

------------------------------------------------------------------------

## Frontend

检查：

-   Browser Console
-   Tauri Log

------------------------------------------------------------------------

## Python

推荐：

    pytest -s

------------------------------------------------------------------------

# 18. Git规范

Commit：

推荐：

    feat:
    fix:
    refactor:
    test:
    docs:
    benchmark:

示例：

    feat: add ACE descriptor plugin

    fix: correct kernel normalization

------------------------------------------------------------------------

# 19. Pull Request要求

必须包含：

-   修改说明
-   测试结果
-   Benchmark结果
-   影响模块

------------------------------------------------------------------------

# 20. 发布流程

Release：

    Tag

    ↓

    Run CI

    ↓

    Build Backend

    ↓

    Build Tauri App

    ↓

    Generate Documentation

    ↓

    Release Package

------------------------------------------------------------------------

# 21. 新开发者Checklist

加入项目：

完成：

-   环境安装
-   编译运行
-   示例计算
-   测试运行
-   阅读架构文档

------------------------------------------------------------------------

# 22. 开发原则总结

MDescriptorStudio开发遵循：

## 科学正确

结果必须可验证。

## 软件工程

代码必须可维护。

## 可扩展

通过Plugin扩展。

## 可复现

记录：

-   数据
-   参数
-   软件版本

------------------------------------------------------------------------

# 最终目标

建立：

    科学计算核心

    +

    开放插件体系

    +

    可复现工作流

    +

    机器学习势研发平台

的长期维护型软件。
