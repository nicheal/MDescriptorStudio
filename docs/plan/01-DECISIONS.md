# 01 · 决策记录（ADR-1～17）

> 状态：全部「已接受」，2026-08-28 定稿
> 产生方式：ADR-1～4 为 v0.1 规划访谈；ADR-5～14 为 grilling 第一轮（10 问）；ADR-15～17 为 grilling 第二轮（3 问）+ 共识确认
> 关联：`PROJECT_PLAN.md` v0.2 §3 为索引；mockup 对照表定稿见本文附录

格式约定：每条 ADR 记「背景 → 决策 → 后果」。后续新决策追加编号，不修改既有编号的语义。

---

## ADR-1 仓库位置：`D:\codex\MD` 根目录

**背景**：独立子目录 `MDescriptorStudio/`（设计文档 §47）与当前工作区根目录二选一。
**决策**：代码放工作区根目录，与 `docs/` 并列（frontend / backend / src-tauri / scripts / tests / README.md）；不采用独立子目录；如需迁移可整体挪出。
**后果**：路径最短，`.venv` 与 `docs/` 天然同仓；设计文档 §47 的目录树按此映射。

## ADR-2 引擎依赖：PyPI 安装，pin `mdescriptor==0.2.3`

**背景**：设计文档 §48/§50 的开发模式假设本地 editable 引擎仓库；实际无本地引擎仓库，PyPI 可达且 cp312 wheel 存在。
**决策**：开发与 Release 一律从 PyPI 安装并 pin `==0.2.3`；按已发布 API 对接（实测完整，见 engine-api-report.md）；升级走 05 文档 §2 四步流程（改 pin → 重跑 probe → diff JSON → 回归）。
**后果**：不再需要「GUI 侧 schema 兜底」与引擎 Phase 0 需求清单；引擎缺陷走上游 issue。

## ADR-3 桌面壳：Tauri 2，无浏览器过渡态

**背景**：设计文档 §6 已定 Tauri 2；v0.1 曾把 Rust/MSVC 安装列为 M0 任务。
**决策**：Rust 1.98 / MSVC / WebView2 已核验就绪，M0 直接搭「Tauri 窗口 + Python sidecar（stdio NDJSON）+ `backend.ready`」完整通路。
**后果**：M0 不含任何安装步骤；风险表删除 MSVC 行。

## ADR-4 UI 基准：`UI.png` mockup 为权威，对照表裁决

**背景**：mockup 与设计文档存在布局/命名冲突；架构、IPC、设计 token 等硬规范仍以设计文档为准。
**决策**：页面布局与信息架构以 mockup 为基准；冲突逐条对照裁决（附录定稿表，10 条）；不改设计文档原文。
**后果**：视觉验收以对照表 + mockup 为准；设计文档与 mockup 不再双轨。

## ADR-5 文档一致性原则：以最新实测为准，过期表述即时清理

**背景**：v0.1 经四轮环境复查后遗留矛盾（M0 仍含安装项、磁盘数字 449/465 不一致、git 重复行、「Phase 0 需求清单」过期约束）。
**决策**：文档随复查轮次即时清理，不保留已被实测推翻的表述；本版已全部清理。
**后果**：以 PROJECT_PLAN v0.2 为唯一现状描述；历史见 `plan/archive/PROJECT_PLAN_v0.1.md`。

## ADR-6 Jobs 不做一级 Tab

**背景**：mockup 有 Jobs Tab + 右上 `Jobs ②` 徽标 + 状态栏三种形态；设计文档 §102 只定义 Drawer；v0.1 标注「Tab 是否保留待定」。
**决策**：v0.1 无 Jobs 一级 Tab——右上徽标 + Drawer（运行中任务/进度/取消）+ 底部状态栏摘要；完整历史检索页留 v0.2 再评估。
**后果**：一级导航固定为 Overview / Explore / Descriptors / Results 四页；M4 范围收窄。

## ADR-7 mockup 对照表补漏五项裁决

**背景**：UI.png 与设计文档逐区比对发现 5 处差异未进 v0.1 对照表（散点图、Property Availability 的 Stress/Magnetic Moment 行、Created/File Size 字段、文档侧 Structure List、Quick Actions 的 Export）。
**决策**：见附录表 #6～#10——散点图与 Export 暂缓至 v0.2；Availability 矩阵行集对齐 DatasetFrame（Stress→Virial，删 Magnetic Moment）；Created/File Size 纳入；Overview 不放 Structure List。
**后果**：对照表定稿为 10 条，覆盖 mockup 全部信息区块，M0/M1 实现无解释空间。

## ADR-8 最小 JobService 前移至 M1

**背景**：设计文档 Rule 9 / §22 将 Dataset Scan/Statistics 定为 Job 类型，但 v0.1 把 JobManager 全放在 M4，M1 统计只能同步阻塞。
**决策**：M1 内置最小 JobService（内存队列 + `job.progress` 事件 + jobs 表落库）；取消与缓存命中 dialog 仍归 M4。
**后果**：M1 统计可异步 + 有进度；协议层从 M1 起就按「>1s 操作返回 job_id」设计（与 ADR-16 一致），M4 无返工。

## ADR-9 测试数据集双轨

**背景**：M2 验收需要 12,480 帧量级数据集；本机仅找到 `D:\Al-Cu\train.xyz`（extxyz，2,000 帧）等小集；设计文档 §54 只要求 ~10 帧 fixture。
**决策**：真实数据 `D:\Al-Cu\train.xyz` 做日常冒烟与只读注册验证；`scripts/` 内生成器产出合成 12,480 帧 DeepMD + extxyz 数据集做 M2 性能验收，同时充当 M1 fixture 来源。
**后果**：不依赖外部数据分发；生成器入 git，CI/回归可复现。

## ADR-10 模糊验收词量化

**背景**：v0.1 的「流畅翻帧」「Cancel 立即生效」无法客观判定。
**决策**：M2 = 12,480 帧上帧切换 p95 < 300ms、3D 首次渲染 < 1s；M4 = Cancel 后 UI 反馈 < 200ms、引擎在下一 cooperative checkpoint 停止并落 CANCELLED。
**后果**：里程碑验收全部可度量。

## ADR-11 输入兼容性预检（设计文档 §8.4 落地）

**背景**：§8.4 要求导入时即可判断描述符兼容性；v0.1 未排期；0.2.3 实测存在「仅 fully_periodic」描述符，有真实触发场景。
**决策**：M1 scan 时将数据集 PBC/periodicity 汇总存入统计缓存；M3 渲染 descriptor 列表时与 `describe_descriptor(name)["input"]` 比对，不兼容项禁用 + 原因 tooltip；提交时 `UNSUPPORTED_PERIODICITY` 兜底。
**后果**：兼容性错误在表单阶段拦截，而不是计算失败后报错。

## ADR-12 日志 / Settings / About 排期

**背景**：设计文档 §45/§52/§105 的需求在 v0.1 里程碑中无落点；M0 调试 sidecar 本身就需要日志。
**决策**：M0 落最小 logging（app.log / backend.log → `%LOCALAPPDATA%\MDescriptorStudio\logs`）；Settings v0.1 仅含默认线程数、数据目录展示、打开日志目录、Dataset Storage 容量条，随 M5；About（三组件版本号）随 M5。
**后果**：三个功能全部有里程碑归属，不再悬空。

## ADR-13 打包风险前移

**背景**：v0.1 风险表漏掉 M5 最可能失败项——PyInstaller 捆绑 mdescriptor 原生 `.libs`/pybind11 扩展；「干净机器验收」未定义手段。
**决策**：风险表新增该条（中）；M4 末尾做一次 PyInstaller 冒烟构建（backend.exe 可启动 + 一次 IPC 往返）；干净机器验收使用 Windows VM（无 VM 则退化为本机清理 `%LOCALAPPDATA%` 后安装测试）。
**后果**：打包问题最晚 M4 末暴露，M5 只做收尾。

## ADR-14 版本管理与 CI

**背景**：v0.1 阶段 1 含 `git init` 但未执行（实测非 git 仓库）；设计文档 §54 提到 CI 但计划未安排。
**决策**：M0 第一步 `git init` + 首个 commit（含 docs/ 与 .gitignore：`.venv/ node_modules/ src-tauri/target/ dist/ __pycache__/` 等）；v0.1 不配外部 CI，以 pytest + `tsc`/`cargo clippy` 本地检查代替；推远程后再补 Actions。
**后果**：历史从 M0 起可追溯；CI 不阻塞单人本地开发。

## ADR-15 规划文档集 JIT 产出

**背景**：v0.1 列了 7 份文档但未定产出顺序；全部前置会拖慢 M0 且 03/05 内容到后期大概率返工。
**决策**：即时（JIT）两波——已出：PROJECT_PLAN v0.2、01-DECISIONS、engine-api-report（+json）、05-ENGINE_ADAPTER；M0 入口条件：02-IPC_PROTOCOL + 04-FRONTEND_DESIGN；M1 入口条件：03-BACKEND_DESIGN。每份设计文档是对应里程碑的启动门。
**后果**：文档与实现互相校准；里程碑不得在其入口文档定稿前开工。

## ADR-16 IPC 协议默认值

**背景**：02 文档编写前需定案四个高返工成本默认值。
**决策**：
1. 并发：允许多个未完成请求并存，响应以请求 `id` 关联；事件独立下行；
2. 长操作：预计 > 1s 的方法（dataset.register/scan、descriptor.submit 等）一律立即返回 `job_id`，结果经事件/查询获取；协议层不同步等待长操作；
3. stdio 帧格式：NDJSON（一行一帧；Python/JS 两侧实现简单，日志可直接 tail）；
4. 错误码：设计文档 §44 的 12 个之外补 `PROTOCOL_VERSION_MISMATCH`、`JOB_NOT_FOUND`、`INVALID_PARAMS`。
**后果**：02-IPC_PROTOCOL.md 按此编写；M0 的 < 100ms 往返验收基于 NDJSON 实现。

## ADR-17 工程约定

**决策**：
1. 语言：代码/标识符/注释英文；文档中文；commit message 英文 conventional commits；
2. JobService 并发：`ThreadPoolExecutor` 线程模型（引擎 compute 为阻塞 C++ 调用），不用 asyncio；
3. SQLite：WAL + 单写者（写经服务层串行化）；
4. 测试：v0.1 仅 backend pytest（adapters/services/protocol）+ tests/ 集成脚本；无前端单测，Vitest/Playwright 留 v0.2；
5. Workspace 持久化：重启恢复 `activeDatasetId`（settings 表），`activeFrameIndex` 归零。
**后果**：写进 00/03 文档与代码评审基线。

---

## 附录：UI.png ↔ 设计文档 对照表（定稿，ADR-4/7）

| # | Mockup（UI.png） | 设计文档 | 定稿裁决 |
|---|---|---|---|
| 1 | 一级导航含 Jobs Tab + 右上 Jobs② 徽标 + 状态栏 | 仅 Drawer（§102） | **无 Tab**（ADR-6）：徽标 + Drawer + 状态栏 |
| 2 | 当前页命名 **Overview** | 称 Dataset Page | 跟随 mockup：Overview |
| 3 | 右侧 Quick Actions 常驻栏 + Recent Jobs 面板 | 无此设计；Inspector 在右 | Quick Actions 收进 Overview 页顶部（导航快捷方式）；Recent Jobs 面板保留在 Overview；**Export Dataset Info 暂缓 v0.2** |
| 4 | 左下 Dataset Storage 容量条 | 无此设计 | 放入 Settings（ADR-12，M5） |
| 5 | Element Distribution 环形图 | 统计清单未提及 | 纳入 Overview，分类数据用科学 categorical palette |
| 6 | Energy/Atom vs Volume 大散点（Max\|F\| 色标） | 无（§88 无散点） | **v0.1 不做**，暂缓至 v0.2 Results 增强 |
| 7 | Property Availability 矩阵含 Stress、Magnetic Moment | §17 仅 Energy/Force/Virial 存在性 | 保留矩阵形式；**Stress→Virial**；**删 Magnetic Moment 行**；Per-Atom/Per-Structure 列由 DatasetFrame 推导 |
| 8 | Created / File Size 字段 | §17 Summary 无 | **两者都加**：Created=注册时间，File Size=scan 汇总 |
| 9 | （反向）Overview 无 Structure List 表 | §88 含 Structure List | 跟随 mockup：**不放**，结构浏览统一走 Explore |
| 10 | 状态栏含 Memory 32 GB | §104 仅版本 + CPU 线程 | 采纳（低成本，随状态栏实现） |
