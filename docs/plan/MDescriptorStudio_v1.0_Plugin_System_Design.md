# MDescriptorStudio v1.0 Plugin System Design

## 1. 设计目标

Plugin System 是 MDescriptorStudio
从单一分析软件升级为科学计算平台的核心。

目标：

支持：

-   Descriptor插件
-   Analysis插件
-   Dataset Adapter插件
-   ML Backend插件
-   Visualization插件

实现：

    Core Framework

          |

    Plugin Interface

          |

    External Extension

------------------------------------------------------------------------

# 2. 插件总体架构

    MDescriptorStudio Core

            |

    Plugin Manager

            |

    --------------------------------

    Descriptor Plugins

    Analysis Plugins

    Dataset Plugins

    ML Plugins

    Visualization Plugins

    --------------------------------

------------------------------------------------------------------------

# 3. Plugin Manager

负责：

-   插件发现
-   插件加载
-   版本检查
-   能力注册
-   生命周期管理

目录：

    plugin/

    manager.py

    registry.py

    loader.py

    schema.py

------------------------------------------------------------------------

# 4. Plugin Metadata

每个插件必须提供：

`plugin.json`

示例：

``` json
{
"name":"ACE Descriptor",

"id":"descriptor.ace",

"version":"1.0.0",

"type":"descriptor",

"api_version":"v1",

"author":"xxx",

"capabilities":[
"periodic",
"atomic",
"gpu"
]
}
```

------------------------------------------------------------------------

# 5. Descriptor Plugin Design

## 5.1 接口

所有Descriptor实现：

``` python
class DescriptorPlugin:


    def name(self):
        pass


    def schema(self):
        pass


    def capabilities(self):
        pass


    def compute(self, structure):
        pass
```

------------------------------------------------------------------------

# 5.2 Descriptor Schema

描述：

-   参数
-   输入要求
-   输出格式
-   物理属性

示例：

``` json
{
"name":"SOAP",

"input":{
"structure":true,
"cell":true,
"pbc":true
},

"parameters":{
"cutoff":5.0,
"nmax":8,
"lmax":6
},

"output":{
"level":"atomic",
"unit":"dimensionless"
}
}
```

------------------------------------------------------------------------

# 5.3 Descriptor Capability

统一能力描述：

``` json
[
"periodic",

"atomic",

"structure",

"forces",

"gpu"
]
```

用于：

-   自动生成UI
-   参数检查
-   工作流匹配

------------------------------------------------------------------------

# 6. Descriptor Backend插件

目录：

    plugins/

    descriptor/

    soap/

    ace/

    acsf/

    mace/

    deepmd/

------------------------------------------------------------------------

## SOAP Plugin

支持：

-   cutoff
-   nmax
-   lmax
-   sigma

------------------------------------------------------------------------

## ACE Plugin

支持：

-   basis
-   degree
-   body order

------------------------------------------------------------------------

## MACE Plugin

支持：

-   equivariant feature
-   message passing

------------------------------------------------------------------------

# 7. Dataset Adapter Plugin

解决：

不同数据格式。

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

支持：

-   EXTXYZ
-   POSCAR
-   CIF
-   LAMMPS dump
-   DeepMD

------------------------------------------------------------------------

# 8. Analysis Plugin

统一接口：

``` python
class AnalysisPlugin:


    def name(self):
        pass


    def run(self,data,parameters):
        pass


    def result_schema(self):
        pass
```

------------------------------------------------------------------------

支持：

-   PCA
-   Kernel
-   Clustering
-   Similarity
-   Feature importance

------------------------------------------------------------------------

# 9. ML Backend Plugin

支持不同机器学习框架。

接口：

``` python
class MLPlugin:


    def train(self,dataset):
        pass


    def predict(self,structure):
        pass


    def evaluate(self):
        pass
```

------------------------------------------------------------------------

支持：

-   MACE
-   NequIP
-   DeepMD
-   ACE potential

------------------------------------------------------------------------

# 10. Plugin生命周期

状态：

    DISCOVERED

         |

    VALIDATED

         |

    LOADED

         |

    ACTIVE

         |

    DISABLED

------------------------------------------------------------------------

# 11. Plugin Registry

保存：

    plugin_registry

    id

    name

    version

    type

    path

    status

    capabilities

------------------------------------------------------------------------

# 12. API注册机制

插件加载后：

自动注册：

    descriptor.soap.compute

    analysis.pca.run

    ml.mace.train

------------------------------------------------------------------------

避免：

核心代码：

``` python
if plugin=="soap":

elif plugin=="ace":
```

------------------------------------------------------------------------

# 13. Plugin版本管理

要求：

记录：

    Plugin version

    API version

    Dependency version

    Algorithm version

保证：

计算结果可复现。

------------------------------------------------------------------------

# 14. Dependency Isolation

推荐：

每个插件：

独立环境：

    plugins/

    soap/

    environment.yml


    ace/

    environment.yml

避免：

不同科学库版本冲突。

------------------------------------------------------------------------

# 15. Plugin安全设计

限制：

-   文件访问权限
-   CPU/GPU资源
-   内存限制
-   超时控制

------------------------------------------------------------------------

# 16. Plugin开发者接口

第三方开发流程：

    Create Plugin

          |

    Implement Interface

          |

    Create plugin.json

          |

    Run Validation

          |

    Install

          |

    Register

------------------------------------------------------------------------

# 17. Plugin测试体系

每个插件必须提供：

    tests/

    unit/

    validation/

    benchmark/

测试：

-   输入输出一致性
-   数值结果
-   性能

------------------------------------------------------------------------

# 18. 最终插件生态

目标：

                     MDescriptorStudio

                             |

                     Plugin Manager

                             |

     ------------------------------------------------

     SOAP     ACE     MACE     DeepMD

     PCA      Kernel  Cluster  Similarity

     POSCAR   XYZ     LAMMPS  CIF

     Training Validation Active Learning

     ------------------------------------------------

------------------------------------------------------------------------

# 总结

Plugin System 是 MDescriptorStudio 长期扩展能力的核心。

设计目标：

从：

    单一科研软件

升级为：

    开放式材料机器学习平台

核心原则：

-   接口稳定
-   算法隔离
-   数据可追溯
-   插件可扩展
-   结果可复现
