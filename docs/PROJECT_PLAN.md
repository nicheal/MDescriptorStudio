# MDescriptor Studio 项目规划（PROJECT PLAN）

> 文档版本：v0.3
> 日期：2026-08-29
> 状态：**M0–M5 实施完成 + 对抗式审查修复完成**（17 项后端集成测试全绿；GUI 实机验证记录见 `docs/plan/VERIFICATION.md`；剩余：干净机安装验证）
> 输入材料：`docs/MDescriptor_GUI_Design.md`（设计基线 v0.1）、`docs/UI.png`（视觉基准 mockup）、`docs/logos.png`（品牌规范）
> 决策方式：grill-me 访谈，4 项关键决策已由项目所有者确认；实施中新增约束见 `docs/plan/05-ENGINE_ADAPTER.md` §6

---

## 1. 项目概述

在已有的 MDescriptor 计算引擎（Python + C++17，PyPI: `MDescriptor`）之上，构建 Windows 桌面应用 **MDescriptor Studio**——面向材料数据集管理、结构浏览、描述符计算与结果分析的 Scientific Workbench。

四层架构（设计文档已定，不再改动）：

```text
React + TS + AntD + Zustand + ECharts + 3Dmol.js   （Presentation）
            ↓ Tauri IPC
Tauri 2                                             （Windows Desktop Shell）
            ↓ JSON IPC / Sidecar (stdio, NDJSON)
Python GUI Controller                               （Dataset / Job / Result / Analysis）
            ↓ Python API
MDescriptor                                         （Descriptor Compute Engine）
```

---

## 2. 环境现状（2026-08-28 实测核验，全部就绪）

| 项目 | 状态 |
|---|---|
| Python | 3.12.9（miniforge3 base）；**项目一律使用 `D:\codex\MD\.venv\Scripts\python.exe`，禁用 base/conda**（项目所有者指定） |
| 项目 .venv | `D:\codex\MD\.venv` 已建立并隔离验证；已装 mdescriptor 0.2.3 + numpy 2.5.2 + pytest 9.1.1，引擎功能实测通过（2026-08-28 复核：runtime_info / 28 描述符 / NEP 空参数实例化） |
| MDescriptor 引擎 | 0.2.3（本地 = PyPI latest），GUI 所需 API 完整；实测报告见 `docs/plan/engine-api-report.md` |
| Node.js / git / Rust / MSVC / WebView2 | v24.16.0 / 2.53.0 / stable 1.98.0 / VS 生成工具 2026 + SDK 10.0.26100 / v151.0.4129.107 —— **全部已安装** |
| OS / 硬件 / 磁盘 | Windows 10 22H2 x64；Ryzen 9 7950X / 63.1 GB；C: 剩 449 GB，D: 剩 1.9 TB |
| 测试数据 | 真实：`D:\Al-Cu\train.xyz`（extxyz，2,000 帧，4 MB）；合成：M1 生成 12,480 帧 DeepMD+extxyz fixture（ADR-9） |

**结论：无环境缺口。**

---

## 3. 决策索引（ADR-1～17）

ADR-1～4 承自 v0.1；ADR-5～17 为 grilling 共识。全文与背景见 `docs/plan/01-DECISIONS.md`。

| ADR | 决策 |
|---|---|
| 1 | 仓库位置：`D:\codex\MD` 根目录（frontend / backend / src-tauri / scripts / tests 与 docs/ 并列） |
| 2 | 引擎依赖：PyPI 安装、pin `mdescriptor==0.2.3`；升级走四步流程（05 文档 §2） |
| 3 | 桌面壳 Tauri 2；Rust/MSVC 已就绪，M0 直接搭「窗口 + sidecar + backend.ready」通路，无浏览器过渡态 |
| 4 | UI 以 `UI.png` mockup 为权威基准；冲突以对照表定稿（10 条，见 01-DECISIONS.md §附录） |
| 5 | 文档一致性：以最新实测为准，过期表述随轮次即时清理（本版已清理：M0 不再含 Rust/MSVC 安装项） |
| 6 | **无 Jobs 一级 Tab**：右上徽标 + Drawer（运行中任务/进度/取消）+ 底部状态栏摘要；完整历史表留 v0.2 |
| 7 | mockup 对照表补漏 5 条裁决（散点图暂缓、Property Availability 对齐数据模型、Created/File Size 纳入、Overview 不放 Structure List、Export 暂缓） |
| 8 | **最小 JobService 前移至 M1**（队列 + progress 事件 + jobs 表）；取消/缓存命中 dialog 仍归 M4 |
| 9 | 测试数据双轨：真实 `D:\Al-Cu\train.xyz`（2,000 帧）做冒烟；脚本生成 12,480 帧合成集做 M2 性能验收，同时充当 M1 fixture |
| 10 | 验收量化：M2 帧切换 p95 < 300ms、首次渲染 < 1s；M4 Cancel UI 反馈 < 200ms、引擎下一 cooperative checkpoint 停止 |
| 11 | 输入兼容性预检：M1 scan 存数据集 PBC/periodicity 汇总 → M3 与 `input` capability 比对禁用不兼容项，提交时 `UNSUPPORTED_PERIODICITY` 兜底 |
| 12 | 日志 M0 落地（`%LOCALAPPDATA%\MDescriptorStudio\logs`）；Settings（线程数/数据目录/打开日志/容量条）与 About 随 M5 |
| 13 | PyInstaller 打包原生 `.libs` 风险前移：M4 末冒烟构建 backend.exe；干净机器验收用 Windows VM |
| 14 | M0 第一步 `git init` + 首个 commit；暂不配外部 CI，以 pytest + tsc/clippy 本地检查代替 |
| 15 | 文档集 JIT 产出：本版 + 01 + engine-api-report + 05 即时；02/04 为 M0 入口条件；03 为 M1 入口条件 |
| 16 | IPC 默认：多请求并发（id 关联）；>1s 操作一律返回 `job_id` 异步化；stdio 帧 = NDJSON；错误码补 PROTOCOL_VERSION_MISMATCH / JOB_NOT_FOUND / INVALID_PARAMS |
| 17 | 工程约定：代码/注释英文、文档中文、commit 英文 conventional；JobService 用 ThreadPoolExecutor；SQLite WAL 单写者；v0.1 仅 backend pytest + 集成脚本（无前端单测）；重启恢复 activeDatasetId、frameIndex 归零 |

---

## 4. 执行计划

### 阶段 1：环境与引擎探测 —— ✅ 已完成

venv 建立、0.2.3 API 探测（`engine-api-report.md`）、Rust/MSVC/WebView2 核验均已完成；`scripts/probe_engine.py` 为可复跑探测工具。

### 阶段 2：规划文档集（JIT，ADR-15）

| 文档 | 状态 | 内容 |
|---|---|---|
| `PROJECT_PLAN.md`（本文件） | ✅ v0.3 | 里程碑、验收、风险 |
| `plan/01-DECISIONS.md` | ✅ | ADR-1～17 全文 + mockup 对照表定稿 |
| `plan/engine-api-report.md` + `.json` | ✅ | 0.2.3 实测 API 面（`scripts/probe_engine.py` 产出） |
| `plan/05-ENGINE_ADAPTER.md` | ✅ | adapter 边界、版本策略、能力/错误映射 |
| `plan/02-IPC_PROTOCOL.md` | ✅ | protocol_version=1、NDJSON 帧、方法目录、事件、错误码全集、sidecar 生命周期 |
| `plan/04-FRONTEND_DESIGN.md` | ✅ | 路由与页面、WorkspaceState、AntD tokens（logos.png 色板）、四基准页线框 |
| `plan/03-BACKEND_DESIGN.md` | ✅ | 包结构、SQLite DDL + migration、DatasetAdapter、JobService 线程模型、存储布局 |

---

## 5. 里程碑路线图

| 里程碑 | 内容 | 验收标准 |
|---|---|---|
| **M0 走通骨架**（入口：02 + 04 定稿） | ① `git init` + 首个 commit（含 docs/、.gitignore）→ ② 最小 logging（app.log/backend.log）→ ③ Tauri 窗口 + Python sidecar（stdio NDJSON）+ `backend.ready` + 启动兼容检查 → ④ 四页空壳（Overview / Explore / Descriptors / Results；无 Jobs Tab，右上徽标占位） | 启动显示空壳页，状态栏显示引擎版本；IPC 往返 < 100ms；日志文件落盘可查 |
| **M1 Dataset**（入口：03 定稿） | DeepMD/extxyz adapter、注册/移除/切换、统计缓存、fingerprint、**最小 JobService**（队列 + `job.progress` + jobs 表，无取消）、scan 记录 PBC/periodicity 汇总、fixture 生成器（~10 帧 ×2 格式 + 合成 12,480 帧） | 10 帧 fixture 注册 < 2s；四张直方图正确；统计以 job 进度呈现；fingerprint 变化触发缓存失效提示 |
| **M2 Explore** | 3Dmol.js viewer、frame 导航（prev/next/random/index）、Structure Info、Atom Table | 合成 12,480 帧上帧切换 p95 < 300ms、首次渲染 < 1s；`D:\Al-Cu\train.xyz` 注册浏览正常；viewer : inspector ≈ 70 : 30 |
| **M3 Descriptor** | registry 动态列表、describe 信息面板、schema 动态表单（8 种参数类型 + **一层嵌套 object** + model 内置/自定义两态）、Compute Scope（当前帧/整数据集）、input capability 预检禁用 | 不硬编码任何 descriptor 名/参数；表单由 schema 生成（含 ACE `trans`/`D` 嵌套）；仅 fully_periodic 描述符对 non-periodic 数据集禁用并显示原因 |
| **M4 Job/Result** | JobManager 全量（协作取消、缓存命中 dialog）、结果落盘（values.npy + metadata.json）、**里程碑末 PyInstaller 冒烟构建 backend.exe** | 大数组不进 JSON IPC；Cancel UI 反馈 < 200ms 且引擎在下一 cooperative checkpoint 停止；重算命中缓存给出选择；backend.exe 可启动并完成一次 IPC 往返 |
| **M5 Analysis + 打包** | PCA、heatmap、PCA 点选 → Explore 反向跳转；Settings（默认线程/数据目录/打开日志/Dataset Storage 容量条）、About；PyInstaller sidecar + Tauri bundle setup.exe | Windows 10/11 **VM** 干净安装即用；PCA→Structure 联动可用；About 正确显示 Studio/Backend/引擎三版本 |

MVP v0.1 明确不做（设计文档 §57 + ADR-7 暂缓项）：UMAP、t-SNE、聚类、FPS、远程 GPU、数据集编辑/转换、轨迹动画、**Export Dataset Info、Overview 的 Energy-Volume 散点图、Jobs 完整历史页**。

---

## 6. 风险表

| 风险 | 等级 | 应对 |
|---|---|---|
| PyInstaller 打包 mdescriptor 原生 `.libs`/pybind11 扩展失败（hidden imports、DLL 捆绑） | 中 | M4 末冒烟构建提前暴露；onedir 模式；必要时补 hook |
| 3Dmol.js 在 Tauri CSP 下的资源加载 | 低 | 资源本地打包，CSP 白名单收窄 |
| ~~PyPI 版 mdescriptor 缺少 GUI 所需 API~~ | ~~高~~ 已消除 | 0.2.3 实测 API 完整（`engine-api-report.md`） |
| ~~MSVC + Rust 安装受阻~~ | — 已消除 | 2026-08-28 核验已安装并编译通过 |

---

## 7. 范围约束

- 不修改 `docs/MDescriptor_GUI_Design.md` 原文（修订只记录在对照表）。
- 不复制数据集原始数据（注册制，原数据只读）。
- 不实现 §57 及 ADR-7 暂缓项。
- MDescriptor 引擎不在本仓库修改：缺陷走上游 issue 报告；若阻塞开发，评估按 ADR-2 四步流程升级 pin，而不是打补丁。

## 8. 工程约定（ADR-17）

- 代码 / 标识符 / 注释：英文；文档：中文；commit message：英文 conventional commits。
- JobService：`ThreadPoolExecutor` 线程模型（引擎 compute 为阻塞 C++ 调用），不用 asyncio。
- SQLite：WAL 模式，单写者（写操作经服务层串行化）。
- 测试：v0.1 仅 backend pytest（adapters / services / protocol）+ `tests/` 集成脚本；前端按里程碑验收标准手动验收，Vitest/Playwright 留 v0.2。
- Workspace 持久化：重启恢复 `activeDatasetId`（settings 表），`activeFrameIndex` 归零。
