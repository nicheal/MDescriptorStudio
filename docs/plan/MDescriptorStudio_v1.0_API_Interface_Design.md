# MDescriptorStudio v1.0 API Interface Design

## 1. API设计目标

MDescriptorStudio采用分层API设计：

    Frontend
        |
        | Tauri IPC
        |
    Backend API Gateway
        |
        |
    Scientific Engine API
        |
        |
    Storage Layer

设计目标：

-   前后端解耦
-   科学计算模块插件化
-   支持异步任务
-   支持长期版本兼容
-   支持第三方Descriptor扩展

------------------------------------------------------------------------

# 2. API分层

## 2.1 Application API

面向Frontend：

负责：

-   页面初始化
-   用户操作
-   任务提交
-   数据查询

## 2.2 Scientific API

面向计算模块：

负责：

-   Dataset
-   Descriptor
-   Analysis
-   ML Workflow

## 2.3 Storage API

负责：

-   数据保存
-   缓存
-   Provenance

------------------------------------------------------------------------

# 3. 通信协议设计

## Request

统一格式：

``` json
{
"id":"request_uuid",
"method":"dataset.list",
"params":{}
}
```

------------------------------------------------------------------------

## Response

成功：

``` json
{
"id":"request_uuid",
"status":"ok",
"result":{}
}
```

失败：

``` json
{
"id":"request_uuid",
"status":"error",
"error":{
"code":"DATASET_NOT_FOUND",
"message":"dataset missing"
}
}
```

------------------------------------------------------------------------

# 4. Dataset API

## 4.1 创建数据集

Method:

    dataset.create

Request:

``` json
{
"path":"sample.xyz",
"name":"Si_MD"
}
```

Response:

``` json
{
"dataset_id":"ds_001"
}
```

------------------------------------------------------------------------

## 4.2 查询数据集

Method:

    dataset.list

返回：

``` json
[
{
"id":"ds_001",
"name":"Si_MD",
"frames":10000,
"fingerprint":"xxxx"
}
]
```

------------------------------------------------------------------------

## 4.3 获取结构

Method:

    dataset.get_frame

参数：

``` json
{
"dataset_id":"ds_001",
"frame":100
}
```

返回：

``` json
{
"positions":[],
"atomic_numbers":[],
"cell":[],
"pbc":[true,true,true]
}
```

------------------------------------------------------------------------

## 4.4 流式读取轨迹

Method:

    dataset.stream_frames

用途：

大规模MD。

支持：

-   chunk
-   offset
-   lazy loading

------------------------------------------------------------------------

# 5. Structure API

统一结构对象：

``` json
{
"positions":[],
"atomic_numbers":[],
"cell":[],
"pbc":[],
"properties":{}
}
```

所有输入格式：

-   POSCAR
-   EXTXYZ
-   LAMMPS
-   DeepMD

转换为该格式。

------------------------------------------------------------------------

# 6. Descriptor API

## 6.1 Descriptor列表

Method:

    descriptor.list

返回：

``` json
[
{
"name":"SOAP",
"version":"2.0",
"capabilities":[
"periodic",
"atomic"
]
}
]
```

------------------------------------------------------------------------

## 6.2 Descriptor信息

Method:

    descriptor.describe

返回：

``` json
{
"name":"ACE",
"cutoff":6.0,
"body_order":4,
"equivariant":true
}
```

------------------------------------------------------------------------

## 6.3 计算Descriptor

Method:

    descriptor.compute

Request:

``` json
{
"dataset_id":"ds001",

"descriptor":{
"name":"SOAP",
"parameters":{
"cutoff":5.0,
"nmax":8,
"lmax":6
}
}
}
```

返回：

``` json
{
"job_id":"job001"
}
```

------------------------------------------------------------------------

# 7. Analysis API

## 7.1 PCA分析

Method:

    analysis.pca

参数：

``` json
{
"descriptor_id":"desc001",
"components":2
}
```

返回：

``` json
{
"variance_ratio":[0.2,0.15],
"embedding":"path"
}
```

------------------------------------------------------------------------

## 7.2 Kernel分析

Method:

    analysis.kernel

支持：

-   similarity
-   effective rank
-   eigen spectrum

------------------------------------------------------------------------

## 7.3 聚类分析

Method:

    analysis.cluster

支持：

-   KMeans
-   DBSCAN
-   hierarchical clustering

------------------------------------------------------------------------

# 8. Selection API

用于训练数据选择。

Method:

    selection.run

参数：

``` json
{
"method":"FPS",
"target_size":5000
}
```

返回：

``` json
{
"selected_frames":[1,5,20]
}
```

------------------------------------------------------------------------

# 9. Job API

所有长任务统一管理。

## 查询任务

    job.get

返回：

``` json
{
"id":"job001",
"status":"RUNNING",
"progress":0.45
}
```

------------------------------------------------------------------------

## 取消任务

    job.cancel

状态：

    RUNNING

    ↓

    CANCEL_REQUESTED

    ↓

    CANCELLED

------------------------------------------------------------------------

# 10. ML Workflow API

## 创建训练任务

Method:

    ml.train

参数：

``` json
{
"model":"MACE",
"dataset":"ds001",
"descriptor":"ACE"
}
```

------------------------------------------------------------------------

## 模型查询

Method:

    model.list

返回：

``` json
[
{
"name":"Si-MACE",
"rmse_energy":0.002
}
]
```

------------------------------------------------------------------------

# 11. Provenance API

查询数据来源。

Method:

    provenance.trace

返回：

    Raw Dataset

    ↓

    Filtered Dataset

    ↓

    Descriptor

    ↓

    Analysis

    ↓

    Model

------------------------------------------------------------------------

# 12. Event API

Backend主动通知Frontend：

事件：

    job.progress

    job.finished

    backend.error

    dataset.changed

示例：

``` json
{
"event":"job.progress",
"job_id":"job001",
"value":0.8
}
```

------------------------------------------------------------------------

# 13. API版本管理

所有API：

包含：

    api_version

例如：

    v1/dataset/list
    v1/descriptor/compute

禁止：

直接修改已有字段。

采用：

新增字段兼容策略。

------------------------------------------------------------------------

# 14. 安全设计

限制：

-   文件路径检查
-   参数验证
-   请求大小限制
-   timeout
-   resource budget

------------------------------------------------------------------------

# 15. 最终API体系

    Dataset API

        |

    Structure API

        |

    Descriptor API

        |

    Analysis API

        |

    Selection API

        |

    ML API

        |

    Provenance API

        |

    Job API

该API设计支持：

-   桌面应用
-   批处理
-   远程计算
-   第三方插件
-   科学工作流自动化
