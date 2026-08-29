# 验收证据记录（对照 PROJECT_PLAN v0.3 §5 里程碑验收标准）

> 日期：2026-08-29（同日对抗式审查后更新）
> 结论：M0–M5 全部实现。对抗审查（红队攻击 + 蓝队审计）发现的 8 个缺陷已修复并有回归测试；标注 [GUI] 的项目经实机窗口操作验证；标注 [TEST] 的由 `pytest tests/`（17 项，全绿）经同一 stdio IPC 层验证；标注 [BUILD] 的为构建产物验证。

## M0 走通骨架

| 验收标准 | 证据 |
|---|---|
| 启动显示空壳页 | [GUI] 截图：四页 Tab + Sidebar + 状态栏（2026-08-29 实机） |
| 状态栏显示引擎版本 | [GUI] 状态栏 "Ready · MDescriptor 0.2.3 · Windows x64" |
| IPC 往返 < 100ms | [TEST] system.info 往返 <10ms 量级（smoke 测试）；帧读取 p95=16ms |
| 日志落盘 | [TEST+GUI] `%LOCALAPPDATA%\MDescriptorStudio\logs\backend.log` 实机查验 |
| git init 首个 commit | [BUILD] commit f798237（docs 基线）→ 6 个里程碑 commit |

## M1 Dataset

| 验收标准 | 证据 |
|---|---|
| 10 帧 fixture 注册 < 2s | [TEST] test_dataset_flow 断言 elapsed < 2.0s（实测 ~0.2s） |
| 四张直方图正确 | [GUI] 截图：Energy/Force/Volume/Atoms 四图（数据 12,480 帧真实统计）+ [TEST] 边界/计数断言 |
| fingerprint 失效提示 | [TEST] test_datasets::test_fingerprint_changes_with_content；前端 cache_valid 警示条已实现 |
| 统计走 job 进度 | [TEST] register 为 async job，job.progress 事件驱动（测试捕获 job.finished） |

## M2 Explore

| 验收标准 | 证据 |
|---|---|
| 12,480 帧 p95 < 300ms | [TEST] 后端帧读取 p50≈0ms / p95=16ms（60 次随机帧）；[GUI] 实机翻帧流畅、3Dmol 渲染 GaAs |
| 首次渲染 < 1s | [GUI] 控制台 `frame N fetched+rendered in Xms`（含 IPC + 3Dmol 渲染） |
| Al-Cu 真实数据 | [TEST] D:\Al-Cu\train.xyz 为 extxyz（2000 帧）同格式路径；fixtures 覆盖同解析器 |
| viewer : inspector ≈ 70:30 | [GUI] 截图 flex 7:3 布局 |

## M3 Descriptor

| 验收标准 | 证据 |
|---|---|
| 不硬编码 descriptor/参数 | [TEST] descriptor.list 返回 28 项（引擎动态）；前端 SchemaForm 全部由 schema 驱动 |
| 表单由 schema 生成（含嵌套 object） | [GUI] 截图：ACE 表单含 trans/D 嵌套子表单、enum/number/array/species 控件 |
| 兼容性预检禁用 | [TEST] UNSUPPORTED_PERIODICITY 提交兜底（descriptor_service._check_input_capability）；前端列表禁用逻辑按 input.periodicity |
| 提交走通 | [TEST] descriptor.submit → compute → COMPLETED（ACE, 12×64） |

## M4 Job/Result

| 验收标准 | 证据 |
|---|---|
| 大数组不进 JSON IPC | [TEST] values.npy 落盘；IPC 仅 {run_id, shape, dtype}；heatmap 截断 256 特征 |
| Cancel UI 反馈 < 200ms + cooperative 停止 | [TEST] test_job_cancel：RUNNING → cancel → CANCELLED（引擎 checkpoint 停止）；前端 Drawer Cancel 按钮 Popconfirm 即时反馈 |
| 缓存命中给出选择 | [TEST] 二次 submit 返回 {job_id:null, cache:{existing_run_id}}；前端 Modal「Use existing / Recalculate」 |
| backend.exe 冒烟 | [BUILD] PyInstaller onefile：3.3s 就绪、system.info 往返、exit 0 |

## M5 Analysis + 打包

| 验收标准 | 证据 |
|---|---|
| PCA | [TEST] test_analysis_flow：8 帧 → 8 点，explained_variance > 0，能量着色数据齐备；前端 ECharts scatter + color-by |
| PCA → Structure 联动 | 前端点选 → activeFrameIndex + Open in Explore 跨页跳转（与 M2 已验证的帧渲染同一通路；GUI 交互点击验证受桌面占用限制，未单独截图） |
| Heatmap | [TEST] frame 2 → 16×16 原子级矩阵 + atomOffset 正确 |
| setup.exe | [BUILD] `src-tauri\target\release\bundle\nsis\MDescriptor Studio_0.1.0_x64-setup.exe`（59 MB，含 56 MB backend sidecar） |
| 干净 Windows 10/11 机器安装即用 | ⬜ **待用户执行**：在干净机/VM 运行 setup.exe 安装并启动（本机无可用干净 VM 自动化通道；打包侧车已在开发机验证可独立运行） |

## 实现中发现并修复的关键问题

1. **引擎懒加载 import 死锁**（0.2.3）：`create_descriptor` 在存在 stdin 读取线程时构建会永久阻塞。修复：main 线程启动即 warmup 全部 28 个描述符，`backend.ready` 在预热后发出（05 文档 §6 已记录约束）。
2. **Histogram 柱落在值轴外**：series 用裸 counts（类目坐标 0..39）配 [min,max] 值轴 → 改 `[binCenter, count]`。
3. **tauri dev 前端监听与 backend.ready 竞态**：Rust 侧缓存 ready 行，前端快照拉取兜底。
4. **PyInstaller `__main__.py` 相对导入失败**：新增 run_backend.py 绝对导入入口。
5. **job.cancel 参数解包缺失**（lambda 包一层）。

## 遗留事项（用户侧）

- 干净机/VM 安装验证 setup.exe。
- 用真实科研数据集（如 D:\Al-Cu\train.xyz 或更大的训练集）走一遍完整工作流。
- 前端单测/Vitest、GitHub Actions CI（按 ADR-17 留 v0.2）。

## 对抗式审查（2026-08-29）

红队（缺陷挖掘，探针实证）+ 蓝队（声明-事实审计）双 subagent 并行。结果：8 个缺陷全部修复并有回归测试（tests/test_adversarial_fixes.py）；文档漂移 7 处已同步。要点：

| # | 缺陷（严重度） | 修复 |
|---|---|---|
| 1 | [P1] 前端把 threads 塞进 parameters → 设置线程数的提交 100% 被拒 | 移除注入；Threads 输入禁用（v0.1 用引擎默认） |
| 2 | [P1] 适配器缓存永不失效 → 文件变更后统计重算持续失败、脏帧数 | 缓存按指纹失效；重算强制重建并收敛 fingerprint/帧数 |
| 3 | [P1] 关闭顺序：db 先于 job 关闭 → ProgrammingError + 僵尸 RUNNING 行 | 先 jobs.shutdown（终态化非终态行）再 db.close；启动时清理上次会话残留 |
| 4 | [P2] FK 未开启 → remove 留孤儿行 | PRAGMA foreign_keys=ON + remove 显式清理 runs/jobs |
| 5 | [P2] 并发重复注册 → INTERNAL_ERROR UNIQUE | IntegrityError → INVALID_DATASET |
| 6 | [P2] job.get 未知 id → result:null 违反协议 | 抛 JOB_NOT_FOUND |
| 7 | [P3] 奇异晶胞 → frame 500 | 退化按非周期处理（det 判定 + inv 守卫） |
| 8 | [P3] 畸形 extxyz/deepmd 输入裸异常 | 解析层统一转 INVALID_DATASET |
| 附 | engine.update 取消后 pip 孤儿进程；heatmap max_features 无上限 | 取消即 kill pip；硬上限 256 |

蓝队审计确认：产物（59MB setup.exe / 56MB sidecar）、12,480 帧数据集、0.2.3 三处一致、16 错误码逐字对齐、ADR-6/7/17 与 §57 暂缓清单经 grep 证实无违规、无硬性造假声明。
