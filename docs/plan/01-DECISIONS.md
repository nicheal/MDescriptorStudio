# 01 · 决策记录（ADR-1～17）

> 状态：ADR-1～25 已接受；Analysis 全文基线于 2026-08-30 定稿
> 产生方式：ADR-1～4 为 v0.1 规划访谈；ADR-5～14 为 grilling 第一轮（10 问）；ADR-15～17 为 grilling 第二轮（3 问）+ 共识确认
> 关联：`PROJECT_PLAN.md` v0.2 §3 为索引；mockup 对照表定稿见本文附录

格式约定：每条 ADR 记「背景 → 决策 → 后果」。后续新决策追加编号，不修改既有编号的语义。

---

## ADR-1 仓库位置：`D:\codex\MD` 根目录

**背景**：独立子目录 `MDescriptorStudio/`（设计文档 §47）与当前工作区根目录二选一。
**决策**：代码放工作区根目录，与 `docs/` 并列（frontend / backend / src-tauri / scripts / tests / README.md）；不采用独立子目录；如需迁移可整体挪出。
**后果**：路径最短，`.venv` 与 `docs/` 天然同仓；设计文档 §47 的目录树按此映射。

## ADR-2 引擎依赖：PyPI 安装，发布用最新版（`mdescriptor>=0.3.2`）

**背景**：设计文档 §48/§50 的开发模式假设本地 editable 引擎仓库；实际无本地引擎仓库，PyPI 可达且 cp312 wheel 存在。0.2.8 发布版曾因 wheel 未携带 CUDA 插件而使 CUDA 选择必报 `DEVICE_UNAVAILABLE`，暴露了固定 pin 滞后于上游修复的问题。
**决策**：开发与 Release 一律从 PyPI 安装 mdescriptor，`backend/requirements.txt` 以 `mdescriptor>=0.3.2`（2026-09-13 由 `==0.2.8` 升级并放开；0.3.2 起 `_cuda.pyd` + `cudart64_12.dll` 随 wheel 发布，下限保证 CUDA 插件在位）约束下限；**发布构建时安装 PyPI 最新版**，开发 `.venv` 已满足下限时 pip 不自动升级；不做 editable 安装；引擎无本地仓库，PyPI 为唯一来源；引擎升级后走 05 文档 §2 流程（重跑 probe → diff JSON → 回归）后再发版。
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
**后果**：以 PROJECT_PLAN 为唯一现状描述（历史版本由 git 历史保留）。

## ADR-6 Jobs 不做一级 Tab

**背景**：mockup 有 Jobs Tab + 右上 `Jobs ②` 徽标 + 状态栏三种形态；设计文档 §102 只定义 Drawer；v0.1 标注「Tab 是否保留待定」。
**决策**：v0.1 无 Jobs 一级 Tab——右上徽标 + Drawer（运行中任务/进度/取消）+ 底部状态栏摘要；完整历史检索页留 v0.2 再评估。
**后果**：一级导航固定为 Overview / Explore / Descriptors / Analysis 四页；旧 Results 页面迁移为 Analysis，M4 的 PCA/heatmap 入口作为兼容层保留。

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

## ADR-18 布局调整：恢复 mockup 右栏与 Jobs Tab（2026-08-29，用户裁决）

**背景**：用户要求按 `docs/UI.png` 调整页面布局，且主内容区默认无滚动条。此前 ADR-6 与附录 #1/#3/#4 曾裁决：无 Jobs Tab、Quick Actions 收进 Overview、Dataset Storage 放 Settings。
**决策**：恢复 mockup 形态——
1. 一级导航加 **Jobs Tab**（`job.list` 历史 + 会话内实时事件合并展示；右上徽标 + Drawer 保留）；
2. **右侧常驻栏**（Quick Actions 4 项 + Recent Jobs 最近 3 条，Running/Queued 优先；窗口 <1280px 自动隐藏）；
3. 左侧栏可折叠（chevron）、Add Dataset 移到列表上方、底部 Dataset Storage 显示已注册数据集体积合计（无磁盘容量 IPC，不伪造容量条）；
4. Overview 重排为三列（Statistics + Element Distribution + Property Availability｜2 直方图｜2 直方图，第 4 张直方图换 Max|Force|，直方图统一主色），整体按视口高度自适应，默认窗口（≥1440×900）零滚动条，过小窗口回退为面板内滚动；
5. Export Dataset Info 以「复制摘要到剪贴板」实现（无文件保存 IPC）；Energy/Atom vs Volume 散点仍按附录 #6 暂缓。
**后果**：右栏与侧栏布局仍保留；其中一级 Jobs Tab 的历史取舍已由 ADR-20 supersede，当前 Jobs 继续使用右上 Drawer。dev 专用浏览器预览入口 `frontend/preview.html`（mock IPC）用于无 Tauri 壳的布局验证。

## ADR-19 DeepMD 数据导入改用 dpdata 包（2026-08-29，用户裁决）

**背景**：`datasets/deepmd.py` 自研 set.*/npy 解析器（memmap 懒加载 + 自造的根目录平铺 npy 兼容）已能工作，但 DeepMD 布局变体多（`nopbc` 标记、混合精度、set 排序细节等），自研维护成本高于直接采用社区标准实现；环境已有 dpdata 1.0.2。
**决策**：DeepMD 数据导入改用 `pip install dpdata`（`dpdata.LabeledSystem/System(fmt="deepmd/npy")` 封装），抛弃项目自研 DeepMD 解析接口；**extxyz 导入保留自研字节偏移解析器不动**。语义随 dpdata：仅支持 `type.raw + set.*/coord.npy` 标准布局（根目录平铺 npy 不再支持），`box.npy` 或 `nopbc` 标记文件必需，`energy.npy` 缺失时按无标注 System 处理（energy/forces/virial 全为否），`type_map.raw` 缺失或含非元素名直接拒绝（描述符引擎需要真实原子序数，不接受 dpdata 的 `Type_N` 假名）。
**后果**：适配器构造时全量载入内存（放弃 memmap 懒加载，大数据集体积 ≈ 帧数×原子数×3×8B×数组数）；dpdata 经动态 importlib 加载格式插件，PyInstaller 改为 `collect_all("dpdata")`（sidecar 体积增大）；fixtures 生成器 `write_deepmd` 改写 set.000 标准布局。回归：27 项 pytest 全绿，含真实 C50Cl1（256 帧）后端进程级端到端。

---

## 附录：UI.png ↔ 设计文档 对照表（定稿，ADR-4/7）

| # | Mockup（UI.png） | 设计文档 | 定稿裁决 |
|---|---|---|---|
| 1 | 一级导航含 Jobs Tab + 右上 Jobs② 徽标 + 状态栏 | 仅 Drawer（§102） | **当前：无 Jobs 一级 Tab**（ADR-20）；Analysis 作为第四页，Jobs 仍由 Drawer 提供 |
| 2 | 当前页命名 **Overview** | 称 Dataset Page | 跟随 mockup：Overview |
| 3 | 右侧 Quick Actions 常驻栏 + Recent Jobs 面板 | 无此设计；Inspector 在右 | Quick Actions 收进 Overview 页顶部（导航快捷方式）；Recent Jobs 面板保留在 Overview；**Export Dataset Info 暂缓 v0.2** → **2026-08 起 ADR-18：恢复常驻右栏，Export=剪贴板导出** |
| 4 | 左下 Dataset Storage 容量条 | 无此设计 | 放入 Settings（ADR-12，M5） → **2026-08 起 ADR-18：侧栏底部显示已注册体积合计（无容量条）** |
| 5 | Element Distribution 环形图 | 统计清单未提及 | 纳入 Overview，分类数据用科学 categorical palette |
| 6 | Energy/Atom vs Volume 大散点（Max\|F\| 色标） | 无（§88 无散点） | Analysis 的 Plotly Projection 取代旧 Results；该 Overview 专用散点仍不在当前范围 |
| 7 | Property Availability 矩阵含 Stress、Magnetic Moment | §17 仅 Energy/Force/Virial 存在性 | 保留矩阵形式；**Stress→Virial**；**删 Magnetic Moment 行**；Per-Atom/Per-Structure 列由 DatasetFrame 推导 |
| 8 | Created / File Size 字段 | §17 Summary 无 | **两者都加**：Created=注册时间，File Size=scan 汇总 |
| 9 | （反向）Overview 无 Structure List 表 | §88 含 Structure List | 跟随 mockup：**不放**，结构浏览统一走 Explore |
| 10 | 状态栏含 Memory 32 GB | §104 仅版本 + CPU 线程 | 采纳（低成本，随状态栏实现） |

## ADR-20 全文 Analysis 取代 Results

**背景**：下载的全文开发文档把描述符空间分析作为产品主线，但现有代码仍以 Results 一级页承载 PCA/heatmap；同时产品所有者明确要求保留 Overview 一级数据集概览。
**决策**：一级导航固定为 Overview / Explore / Descriptors / Analysis。Results 改名为 Analysis；旧 PCA/heatmap 进入 Analysis 的 Projection，并保留 result.get_pca/result.heatmap 兼容入口。
**后果**：旧客户端和历史 artifact 不被破坏，新的分析模块拥有统一页面和结果模型。

## ADR-21 Analysis 数值与传输边界

**决策**：Analysis 后端以 float64 计算并完整落盘；IPC 只返回最多 20,000 点的 preview 或受限 chunk；任何算法都不得默认生成完整 N×N 距离矩阵。计算采用 CPU-first 分块策略。
**后果**：浏览器负载和内存增长有上限，用户仍可通过分页查看完整数组。

## ADR-22 统一结果 artifact 与缓存

**决策**：每个 AnalysisResult 由 SQLite 元数据、metadata.json、manifest.json 和 named .npy 数组组成。先写随机临时目录，manifest 完整后原子改名；缓存键由输入 Run IDs、规范化参数和 algorithm version 组成。
**后果**：崩溃留下的临时目录不能成为完成结果，算法升级会自然失效旧缓存。

## ADR-23 源数据变化与 STALE

**决策**：dataset fingerprint 变化时旧 Descriptor/Analysis Run 保留并标记 STALE。STALE 结果可读、可审计、可删除，但不能作为新分析输入；完成 rescan/recompute 后才允许新 Run。
**后果**：结果历史不会被静默删除，也不会混入新数据。

## ADR-24 分析依赖与离线发布

**决策**：scikit-learn、hdbscan 固定在 backend/requirements.txt，并通过 PyInstaller spec 进入 sidecar；UMAP 由 Studio 内置 numpy/scipy 实现（analysis/umap_numpy.py，2026-09 起），不引入 umap-learn/numba/llvmlite；Plotly 作为前端本地依赖，仅 Analysis 使用。
**后果**：安装后的 Analysis 不依赖联网下载包，Overview/Explore 的现有图形技术保持不变；numba JIT 缓存投毒面（RT-01）随依赖移除而消除，UMAP 不再有冷启动 JIT 编译与单线程 random_state 限制。

## ADR-25 选择、导出与跨 Run 比较

**决策**：Plotly 支持 click 单点与 box/lasso 框选；Sampling 导出 JSON/CSV 身份文件和 DeepMD/extxyz 子集，源文件只读。Compare 在 feature count 相同时做 feature-level 指标；不同描述符只有 sample IDs 对齐时才做 distance/ranking correlation。Parameter Sensitivity 只比较同一描述符的已有 Completed Run；不同描述符使用 Compare。
**后果**：用户可从散点选择直接回到 Explore，跨描述符不会通过补零/截断伪造 feature-level 可比性。

## ADR-26 计算设备选择（0.2.8 起）

2026-09-07 更新：已开放 CPU 描述符线程设置（schema 声明支持时）；`num_threads` 可留空或设为 1–64 的整数。显式值传到引擎 execution，参与缓存键并写入结果 metadata。留空保持原缓存键。下述原决策中的线程默认限制已解除。

**背景**：mdescriptor 0.2.8 为 28/28 描述符声明 `execution.devices: ["cpu","cuda"]`（0.2.7 及之前全 `["cpu"]`）；设备入口为配置保留键 `execution`（引擎还原为 `ExecutionOptions(device=...)`）。
**决策**：Descriptor 页 Execution 区的设备下拉按 schema 声明列表渲染（唯一 cpu 时保持禁用单选，不硬编码设备名）；选择经 `descriptor.submit` 的 `device` 提交，服务端按 schema 校验（未声明 → `INVALID_PARAMS`）并计入缓存键与结果 metadata（`descriptor_runs.device` 列，migration 5）；默认 `"cpu"`，`num_threads` 仍用引擎默认。声明了但本机无运行时的设备在计算期报 `DEVICE_UNAVAILABLE`（引擎 `code=device_unavailable` 的映射）。
**后果**：CPU/CUDA 结果互不命中缓存，可审计；CUDA 计算路径的正确性验收需 CUDA 硬件（开发机无 GPU，仅验证了不可用路径的错误呈现），首次 GPU 验收前 UI 不做任何 CUDA 可用性预判。

## ADR-27 Job 类别线程池与协作式关停（2026-09-06）

**背景**：JobService 此前为全局 2 线程 FIFO：被取消但仍卡在原生调用里的「僵尸计算」可占满全部工作线程；`shutdown()` 不协作取消且非 daemon 线程会在解释器退出时 join，卡住的原生调用可挂住后端进程；`compute.default_threads` 设置无任何后端读取。
**决策**：JobService 改为按类别的三个线程池——`engine`（1 线程，承载 `descriptor.compute`）、`analysis`（2）、`dataset`（2，兜底未知类型）；`submit` 签名与队列背压不变。`shutdown()` 先对所有存活 context 调 `cancel()`（触发引擎 ComputeControl 取消并结算 run 行）再关池，`main()` 在清理完成后 `os._exit` 绕过 atexit join。RUNNING 状态更新补 `AND status='QUEUED'` 守卫。`job.get`/`job.list` 加入 RPC 控制通道；`descriptor.submit` 增加在途去重（`_submit_lock` + cache_key JOIN 查询，`force` 绕过）；`_load_samples`/`_pool_per_structure` 增加取消检查点并改用 `np.add.reduceat` 向量化池化；`result.heatmap` 改 mmap 读取；`compute.default_threads` 经 threadpoolctl 作用于分析计算（进程级、不恢复，描述符引擎线程仍由引擎管理）。QUEUED 任务在 `job.get`/`job.list` 附带 `queue_position`（按类别池内排队序，created_at 秒级精度下用 rowid 次级排序），前端 JobsDrawer 显示「第 N 位」。
**后果**：最坏并发从全局 2 变为按类别 1+2+2；类别内 FIFO 不变。僵尸计算的最坏影响被限制在 engine 池内；关停不再依赖「计算及时返回」；values 结果 LRU 缓存与僵尸池占用可视化暂缓（见 docs/plan/scheduling-fix-plan.md）。
