# MDescriptorStudio v1.0 Migration Implementation Plan

## 1. 文档目标

本文档用于指导 MDescriptorStudio 从当前代码版本迁移到 v1.0 目标架构。

目标：

    Legacy Code

    ↓

    Incremental Refactoring

    ↓

    Modular Architecture

    ↓

    MDescriptorStudio v1.0

原则：

-   不破坏已有功能
-   分阶段迁移
-   保持科学结果一致
-   每一步可回滚

------------------------------------------------------------------------

# 2. 迁移总体策略

采用渐进式重构：

    Phase 0

    代码冻结与备份

    ↓

    Phase 1

    基础设施重构

    ↓

    Phase 2

    核心模块拆分

    ↓

    Phase 3

    Plugin化

    ↓

    Phase 4

    Workflow升级

    ↓

    Phase 5

    AI扩展

------------------------------------------------------------------------

# 3. Phase 0：代码基线建立

## 目标

建立可靠迁移起点。

任务：

-   固定当前release版本
-   创建backup branch
-   完成功能清单
-   建立测试基准

Git策略：

    main

    |

    release/v0.x

    |

    refactor/v1.0

------------------------------------------------------------------------

# 4. Phase 1：基础工程重构

## 目标

提高代码可维护性。

任务：

## 目录整理

现状：后端已作为包组织在 `mdescriptor_studio_backend/` 之下，测试集中于 `tests/`，
CI 已在运行。真实缺口是仓库根目录缺 `pyproject.toml`、`LICENSE` 等发布要件，
而非目录混乱。

目标：

    src/

    ├── core

    ├── plugins

    ├── services

    ├── interface

    └── tests

------------------------------------------------------------------------

## 配置系统统一 —— 已完成

`config.py` 已集中管理数据目录布局与 `PROTOCOL_VERSION`，支持 `MDS_DATA_DIR`
覆盖并对路径做安全校验。无需再引入 `ConfigManager`，原诊断的"硬编码参数、
分散配置文件"已不成立。

------------------------------------------------------------------------

## 日志系统 —— 已完成

`logging_setup.py` 统一配置，各模块使用 `getLogger(__name__)`，
Debug / Info / Warning / Error 分级与日志落盘已就位。

    Logger

    |

    Debug

    Info

    Warning

    Error

------------------------------------------------------------------------

# 5. Phase 2：核心数据层迁移

## 目标

建立统一数据模型。

新增：

    models/

    ├── material.py

    ├── structure.py

    ├── dataset.py

    ├── descriptor.py

    └── result.py

------------------------------------------------------------------------

## 数据对象设计

统一：

    DatasetObject

    =

    Structure

    +

    Metadata

    +

    Properties

    +

    Provenance

------------------------------------------------------------------------

# 6. Phase 3：Descriptor系统重构 —— 已完成，本阶段取消

## 当前问题

原诊断"不同算法接口不统一"已不成立。接口统一由引擎契约提供：

-   版本化 JSON schema：28 个描述符 / 170 个参数 + 能力位
    （`input.mixed_periodicity`、`execution.devices`、`execution.cooperative_cancel`）
-   `mdescriptor_adapter.py` 按名字动态派发：`list_descriptors()` →
    `describe_descriptor(name)` → `create_descriptor(cfg)`
-   新增描述符不需要改动 Studio 代码

下文拟建的 `BaseDescriptor` 与 `calculate() / save() / load() / metadata()`
已在引擎侧等价存在。本节保留仅作决策记录，勿据此开工。

------------------------------------------------------------------------

## 新设计

    BaseDescriptor

            |

    -----------------

    SOAP

    ACE

    ACSF

    Custom

------------------------------------------------------------------------

统一接口：

``` python
calculate()

save()

load()

metadata()
```

------------------------------------------------------------------------

# 7. Phase 4：Analysis模块迁移

现状：本阶段的"统一接口"目标已在事实上达成 —— `analysis/algorithms/` 下
pca / kernel / feature_correlation / property_correlation / tsne / umap
全部采用同一签名 `(samples: DescriptorMatrix, params: dict, progress) -> dict`，
并共享 `analysis/models.py` 的 `DescriptorMatrix`。

因此不需要引入 `AnalyzerBase` 类层次；`analysis_runs` 已带
`algorithm_version`（迁移 3）、`cache_key`、`warnings_json` 等列，
本阶段仅剩把这些字段聚合为显式 `AnalysisObject`。

统一：

    AnalyzerBase

支持：

-   PCA
-   Kernel Analysis
-   Similarity
-   Clustering

接口：

    fit()

    transform()

    visualize()

    export()

------------------------------------------------------------------------

# 8. Phase 5：Workflow Engine建设

目标：

从脚本执行转向流程管理。

设计：

    Workflow

     |

    Task

     |

    Executor

------------------------------------------------------------------------

支持：

-   DAG
-   状态管理
-   checkpoint
-   error recovery

------------------------------------------------------------------------

# 9. Phase 6：Plugin系统迁移

插件结构：

    plugins/

    descriptor/

    analysis/

    simulation/

    visualization/

------------------------------------------------------------------------

插件必须提供：

    plugin.yaml

    interface.py

    tests/

------------------------------------------------------------------------

# 10. Phase 7：API层建设 —— 已完成

目标：

解耦GUI和核心 —— 已达成：前端与后端为两个进程，仅经版本化 IPC 协议通信
（`protocol/server.py`，`PROTOCOL_VERSION = 1`），后端不依赖任何 UI 代码。

架构：

    Frontend

    ↓

    API

    ↓

    Core Engine

------------------------------------------------------------------------

接口：

    submit_task()

    get_result()

    load_dataset()

    run_analysis()

------------------------------------------------------------------------

# 11. Phase 8：测试体系建立

已建立，非空白：32 个 `test_*.py`（255 项通过、1 项跳过）+ `tests/numerical/`
（hypothesis、fuzz）+ `tests/regression/`（`descriptor.npy` + `expected.npz` 数值基线）
+ `frontend/e2e/` Playwright 端到端用例，CI 已在运行。

本节真实缺口只有两项：Benchmark（见 Testing_and_Benchmark_Design）
与 unit / integration 的目录分层。

## Unit Test

覆盖：

-   Data Object
-   Descriptor
-   Analyzer

## Integration Test

测试：

完整Workflow。

## Regression Test

保证：

旧版本科学结果一致。

------------------------------------------------------------------------

# 12. Phase 9：性能优化

顺序：

第一阶段：

-   Cache
-   Vectorization

第二阶段：

-   Multiprocessing

第三阶段：

-   GPU
-   Distributed Computing

------------------------------------------------------------------------

# 13. Git开发流程

推荐：

    main

    |

    develop

    |

    feature/*

    |

    review

    |

    merge

------------------------------------------------------------------------

Commit规范：

    feat:

    add new feature


    fix:

    bug fix


    refactor:

    code restructuring


    test:

    add test

------------------------------------------------------------------------

# 14. 每阶段验收标准

## Phase 1

代码结构稳定。

## Phase 2

数据接口统一。

## Phase 3

Descriptor插件化。

## Phase 4

Workflow自动化。

## Phase 5

AI接口可扩展。

------------------------------------------------------------------------

# 15. 风险控制

## 风险1

重构导致结果变化。

解决：

建立：

    Scientific Benchmark Dataset

------------------------------------------------------------------------

## 风险2

开发周期过长。

解决：

增量迁移。

------------------------------------------------------------------------

## 风险3

新架构复杂。

解决：

保持：

Core简单。

------------------------------------------------------------------------

# 16. 最终v1.0架构

    MDescriptorStudio

    |

    Core Engine

    |

    Data Model

    |

    Descriptor Plugin

    |

    Analysis Plugin

    |

    Workflow Engine

    |

    API Layer

    |

    GUI

------------------------------------------------------------------------

# 总结

Migration Implementation Plan 提供从旧代码到新架构的工程实施路径。

核心原则：

    Measure

    ↓

    Refactor

    ↓

    Test

    ↓

    Release

确保 MDescriptorStudio 平稳演进为下一代材料科学计算平台。
