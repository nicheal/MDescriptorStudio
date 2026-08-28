# MDescriptor Studio 项目规划（PROJECT PLAN）

> 文档版本：v0.1
> 日期：2026-08-28
> 状态：规划评审中（尚未开始实施）
> 输入材料：`docs/MDescriptor_GUI_Design.md`（设计基线 v0.1）、`docs/UI.png`（视觉基准 mockup）、`docs/logos.png`（品牌规范）
> 决策方式：grill-me 访谈，4 项关键决策已由项目所有者确认

---

## 1. 项目概述

在已有的 MDescriptor 计算引擎（Python + C++17，PyPI: `MDescriptor`）之上，构建 Windows 桌面应用 **MDescriptor Studio**——面向材料数据集管理、结构浏览、描述符计算与结果分析的 Scientific Workbench。

四层架构（设计文档已定，不再改动）：

```text
React + TS + AntD + Zustand + ECharts + 3Dmol.js   （Presentation）
            ↓ Tauri IPC
Tauri 2                                             （Windows Desktop Shell）
            ↓ JSON IPC / Sidecar (stdio)
Python GUI Controller                               （Dataset / Job / Result / Analysis）
            ↓ Python API
MDescriptor                                         （Descriptor Compute Engine）
```

---

## 2. 环境现状调查（2026-08-28 第二轮实测复查）

| 项目 | 状态 | 影响 |
|---|---|---|
| Python | 3.12.9（miniforge3 base，pip 26.2.1，含 ase 3.26 / numpy / pandas） | ✅ 可用 |
| conda | 25.9.0；envs：mlff310 / mlffgui / nepkit | ✅ 可用 |
| **mdescriptor** | **0.2.3 已安装在 base**（初查误报未装），含原生 `.libs`；**GUI 依赖的 API 完整**：`describe_descriptor`、`get_runtime_info`、`list_descriptors`、`create_descriptor`、`StructureBatch`、`DescriptorResult`、`DescriptorRegistry`、`DescriptorConfiguration`、`ExecutionOptions`、`OutputOptions`、`ComputeControl`、全部 schema version 常量与错误类型 | ✅ 重大利好：设计文档 §63 所述 Phase 0 引擎补充已在 0.2.3 完成 |
| 引擎注册表 | `list_descriptors()` 返回 **28 个描述符**（SOAP、SOAPTurbo、ACSF、ACE、MBTR、LMBTR、MTP、NEP、DPA4 等） | GUI 无需硬编码，schema 实测与文档 §8.1 契约一致且更丰富（display_name/category/capabilities/嵌套对象参数） |
| PyPI | 可达（HTTP 200）；MDescriptor latest = **0.2.3**（与本地一致），提供 **cp312 win_amd64 wheel（32 MB）**；requires_python >=3.10；核心依赖仅 numpy / array-api-compat / packaging | ✅ `.venv` 可直接装 0.2.3 |
| Node.js | v24.16.0 + npm 11.16.0 | ✅ 可用 |
| git | 2.53.0.windows.2 | ✅ 可用 |
| **项目 .venv** | **已建立 `D:\codex\MD\.venv`**（Python 3.12.9，隔离确认：prefix=.venv，不含系统站点包）；已装 **MDescriptor 0.2.3 + numpy 2.5.2 + pytest 9.1.1**，引擎在 venv 内功能验证通过（runtime_info / 28 描述符 / ACE 四段 schema / ComputeControl 实例化） | ✅ 引擎栈就绪 |
| **开发约定** | **本项目所有 Python 调用一律使用 `.venv\Scripts\python.exe`（或先激活 venv），禁止使用 base/conda env** | 已由项目所有者指定（2026-08-28） |
| Rust 工具链 | ✅ stable 1.98.0（rustup 1.29.0，host/target = x86_64-pc-windows-msvc） | Tauri 前置一已解决 |
| MSVC Build Tools | ✅ **已安装**（2026-08-28 第四轮核对确认）：Visual Studio 生成工具 2026（v18，含 VC.Tools.x86.x64）+ Windows SDK 10.0.26100.0 | Tauri 前置二已解决 |
| 编译链实测 | `rustc` 编译链接运行通过；`cargo new` + `cargo build` 全链路通过（Hello, world 输出） | ✅ Rust→exe 全链路可用 |
| WebView2 Runtime | **已安装 v151.0.4129.107** | ✅ Tauri 可直接运行 |
| git | 2.53.0.windows.2 | ✅ 可用 |
| OS | Windows 10 22H2（10.0.19045.6466）x64 | ✅ 满足目标平台 |
| 硬件 | AMD Ryzen 9 7950X（32 逻辑核）/ 63.1 GB RAM | ✅ 编译与计算充裕 |
| 磁盘 | C: 剩 449 GB（VS 安装后）；D: 剩 1.9 TB | ✅ 充裕 |
| `gui-adaptation-baseline.md` | 不存在（设计文档引用了它） | 基线字段以设计文档 §8 为准；0.2.3 实测 schema 即事实基线 |

**结论（2026-08-28 第四轮核对后）**：**环境全部就绪，无剩余缺口**——Python(.venv) / Node / Rust / MSVC / WebView2 / PyPI 全部可用并经实测验证；M0 可直接开始。

---

## 3. 已确认的四项关键决策（ADR）

### ADR-1 仓库位置：`D:\codex\MD` 根目录

项目代码直接放在当前工作区根目录，与 `docs/` 并列：

```text
D:\codex\MD\
├── docs/            # 现有设计文档 + 本规划 + 后续 plan/ 文档集
├── frontend/        # React + TS + Vite
├── backend/         # mdescriptor_studio_backend（Python sidecar）
├── src-tauri/       # Tauri 2 壳
├── scripts/         # 构建/开发脚本
├── tests/           # 集成测试 + 极小 fixture 数据集
└── README.md
```

不采用独立子目录 `MDescriptorStudio/`；后续如需迁移可整体挪出。

### ADR-2 引擎依赖：直接从 PyPI 安装对接（已验证可行）

- 在项目 `.venv` 中 `pip install mdescriptor==0.2.3`（cp312 win_amd64 wheel 已确认存在），按**现有已发布 API** 对接。
- ~~风险：`describe_descriptor()`、`get_runtime_info()` 等接口可能尚未发布~~ **已消除**：base 环境实测 0.2.3 的 API 完整覆盖 GUI 所需（describe_descriptor / get_runtime_info / list_descriptors / ComputeControl / 全部 schema 版本常量），28 个描述符全部带机器可读 schema。
- 剩余工作仅为 adapter 边界设计（`05-ENGINE_ADAPTER.md`），不再需要 GUI 侧 schema 兜底与 Phase 0 需求清单。
- 版本策略：GUI 开发期 pin `mdescriptor==0.2.3`，与设计文档 §50「Release 固定引擎版本」一致。

### ADR-3 桌面壳：按原设计上 Tauri 2

- 计划包含安装 Rust + MSVC Build Tools（安装器需人工交互确认，放在 M0）。
- M0 即搭出「Tauri 窗口 + Python sidecar（stdio JSON IPC）+ `backend.ready`」完整通路，不留浏览器过渡态。

### ADR-4 UI 基准：`UI.png` mockup 为准，出对照表

- 页面布局与信息架构以 mockup 为权威基准；与设计文档冲突处出逐条对照表并给出裁决。
- 架构 / IPC 协议 / 设计 token 等硬规范仍以 `MDescriptor_GUI_Design.md` 为准。
- 已识别的冲突条目（待对照表定稿）：

| # | Mockup（UI.png） | 设计文档 | 初步裁决方向 |
|---|---|---|---|
| 1 | 一级导航含 **Jobs Tab**（Overview/Explore/Descriptors/Results/Jobs） | 一级页面只有 4 个，Jobs 为右侧 Drawer + 底部状态栏 | 保留 Drawer + 状态栏；Tab 是否保留待定 |
| 2 | 当前页命名 **Overview**（数据集统计页） | 称 Dataset Page | 跟随 mockup 改名 Overview |
| 3 | 右侧 **Quick Actions** 常驻侧栏 + Recent Jobs 面板 | 无此设计；Inspector 在右侧 | Quick Actions 收进 Overview 页或 Inspector，不全局常驻 |
| 4 | 左下 **Dataset Storage** 容量条 | 无此设计 | 低优先，可放入 Settings |
| 5 | Element Distribution 用环形图 | 统计清单未提及 | 可加，遵循科学配色原则 |

---

## 4. 执行计划（批准后的两个阶段）

### 阶段 1：环境与引擎探测

1. **建立项目虚拟环境**（项目所有者已明确要求）：
   - `python -m venv .venv`（`D:\codex\MD\.venv`，基于本机 Python 3.12.9）
   - `pip install mdescriptor==0.2.3 numpy pytest`（cp312 wheel 已确认存在，核心依赖轻量）
2. **引擎 API 探测**：✅ 已于 2026-08-28 复查中在 base 环境完成（API 完整、28 个描述符、schema 与文档契约一致）；`.venv` 建好后仅需复核版本一致性，产出归档到 `docs/plan/engine-api-report.md`
3. **仓库初始化**：`git init` + `.gitignore`（`.venv/`、`node_modules/`、`src-tauri/target/`、`dist/`、`__pycache__/` 等）
4. **Tauri 前置安装**（唯一环境缺口，M0 执行）：Rustup + MSVC Build Tools（约 10 GB，安装器需人工交互确认）

### 阶段 2：详细规划文档集 `docs/plan/`

| 文档 | 内容 |
|---|---|
| `00-PROJECT_PLAN.md` | 里程碑 M0–M5 工作包、验收标准、风险表（本文件的细化版） |
| `01-DECISIONS.md` | ADR 全文 + UI mockup 对照表定稿 |
| `02-IPC_PROTOCOL.md` | protocol_version=1：请求/响应/错误帧、方法目录（`system.info`、`dataset.*`、`descriptor.*`、`job.*`、`result.*`、`analysis.pca`）、事件（`backend.ready`、`job.progress`）、错误码全集、stdio 帧格式与 sidecar 生命周期 |
| `03-BACKEND_DESIGN.md` | backend 包结构、SQLite 全表 DDL + migration 框架、DatasetAdapter 接口与 DeepMD/extxyz 实现、JobService 线程/取消模型、`%LOCALAPPDATA%\MDescriptorStudio\` 存储布局 |
| `04-FRONTEND_DESIGN.md` | 路由与页面清单、WorkspaceState（Zustand）、目录结构、AntD ConfigProvider tokens（融合文档 §64–133 与 logos.png 色板 #0F6CBD / #0A3D91 / #00B8A9 / #2ECC71 / #667085 / #E5E7EB）、四基准页面线框 |
| `05-ENGINE_ADAPTER.md` | `mdescriptor_adapter` 单点边界、0.2.3 API 探测报告引用、版本 pin 策略、能力探测（devices/cooperative_cancel）的 adapter 映射 |
| `engine-api-report.md` | PyPI 版 mdescriptor 实测 API 面（阶段 1 产出） |

---

## 5. 里程碑路线图

| 里程碑 | 内容 | 验收标准 |
|---|---|---|
| **M0 走通骨架** | 安装 Rust+MSVC → Tauri 窗口 + Python sidecar（stdio JSON IPC）+ `backend.ready` + 四页空壳布局 | 启动后窗口显示空壳页，状态栏显示引擎版本；IPC 往返 < 100ms |
| **M1 Dataset** | DeepMD/extxyz adapter、注册/移除/切换、统计缓存、fingerprint | 注册 10 帧 fixture 数据集 < 2s；统计页四张直方图正确；fingerprint 变化触发缓存失效提示 |
| **M2 Explore** | 3Dmol.js viewer、frame 导航（prev/next/random/index）、Structure Info、Atom Table | 12,480 帧数据集流畅翻帧；viewer : inspector ≈ 70 : 30 |
| **M3 Descriptor** | registry 动态列表、describe 信息面板、schema 动态表单、Compute Scope（当前帧/整数据集）、提交 | 不硬编码任何 descriptor 名/参数；表单由 schema 生成 |
| **M4 Job/Result** | JobManager、进度/协作取消、结果落盘（values.npy + metadata.json）、缓存命中 dialog | 大数组不进 JSON IPC；Cancel 立即生效（有 cooperative_cancel 时）；重算命中缓存给出选择 |
| **M5 Analysis + 打包** | PCA、heatmap、PCA 点选 → Explore 反向跳转；PyInstaller sidecar + Tauri bundle setup.exe | 干净 Windows 10/11 机器安装即用；PCA→Structure 联动可用 |

MVP v0.1 明确不做（遵循设计文档 §57）：UMAP、t-SNE、聚类、FPS、远程 GPU、数据集编辑/转换、轨迹动画等。

---

## 6. 风险表

| 风险 | 等级 | 应对 |
|---|---|---|
| ~~PyPI 版 mdescriptor 缺少 `describe_descriptor`/`get_runtime_info`~~ | ~~高~~ → **已消除** | 2026-08-28 实测 0.2.3 API 完整（28 描述符、schema 契约一致） |
| MSVC + Rust 安装受阻（网络/磁盘/权限） | 中 | 磁盘充裕（C: 465 GB）；M0 预留时间并文档化安装步骤 |
| 3Dmol.js 在 Tauri CSP 下的资源加载 | 低 | 资源本地打包，CSP 白名单收窄 |
| mockup 与文档冲突引起反复 | 低 | ADR-4 对照表一次裁决，文档原文不改 |

---

## 7. 范围约束

- 不修改 `docs/MDescriptor_GUI_Design.md` 原文（修订只记录在对照表）。
- 不复制数据集原始数据（注册制，原数据只读）。
- 不实现设计文档 §57 列出的 v0.1 暂缓项。
- MDescriptor 引擎仓库的改动（Phase 0 API）不在本仓库进行，只输出需求清单。
