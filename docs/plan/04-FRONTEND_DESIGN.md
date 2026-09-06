# 04 · Frontend Design

> 状态：定稿（M0 入口条件）
> 依据：设计文档 §15/§64–133、UI.png、logos.png、ADR-6/7/17

## 1. 技术与目录

React 18 + TypeScript + Vite + AntD 5 + Zustand + ECharts + Plotly + 3Dmol.js。

```text
frontend/src/
├── main.tsx / App.tsx          # 布局骨架 + 页面切换
├── theme.ts                    # AntD ConfigProvider tokens（§4）
├── ipc/
│   ├── tauri.ts                # invoke/listen 薄封装
│   └── client.ts               # NDJSON IPC：pending id 关联 + 事件订阅
├── stores/workspace.ts         # Zustand WorkspaceState + backend 状态
├── types/                      # protocol.ts / dataset.ts / descriptor.ts / job.ts
├── components/
│   ├── layout/  Sidebar.tsx ContextBar.tsx StatusBar.tsx JobsBadge.tsx
│   └── ...
└── pages/  Overview.tsx Explore.tsx Descriptors.tsx Analysis.tsx Results.tsx Settings.tsx About.tsx
```

## 2. 布局骨架（2026-08 ADR-18：恢复 mockup 右栏与 Jobs Tab；原 ADR-6 裁决被取代）

```text
┌──────────────────────────────────────────────────────────────┐
│ M Left: Sidebar 240px          Jobs② ⚙        (原生标题栏)     │
├──────────────┬───────────────────────────────────────────────┤
│ DATASETS 〈  │ ContextBar 两行：大标题 + chips（格式 · N ·    │
│ 搜索 + Add   │ 元素 · PBC · E/F/V ✓chips · 路径 · ⋯菜单）     │
│ 数据集列表    ├───────────────────────────────────────────────┤
│              │ Tabs: Overview | Explore | Descriptors |       │
│ Dataset      │        Analysis | Jobs                         │
│ Storage 合计  ├──────────────────────────────┬────────────────┤
│              │ Main Workspace（页面内容）     │ RightRail 264px │
│              │ 高度自适应视口，默认零滚动条    │ Data Health     │
│              │ [+ Inspector]                 │ + Rescan        │
├──────────────┴──────────────────────────────┴────────────────┤
│ StatusBar: ● Ready | MDescriptor 0.3.2 | CPU n threads        │
└──────────────────────────────────────────────────────────────┘
```

- Jobs 徽标 + Drawer（M4）：Drawer 展示运行中任务、进度条 4–6px、Cancel；Jobs Tab 展示 `job.list` 历史 + 会话实时事件合并表。
- RightRail 264px 常驻（**Data Health 面板**：Missing values / Invalid cell / Duplicate structures / Extreme force 四项检查（0=绿勾，>0=橙警告，计数+占 structures 百分比）+ Scan status（Completed/Scanning…/Pending + 时间）+ Rescan 按钮，`dataset.statistics` 缓存读取、`dataset.rescan` 强制重扫），<1280px 宽自动隐藏。2026-08 起取代原 Quick Actions + Recent Jobs（任务历史归 Jobs Tab）。
- Inspector 280px，可折叠（`>` 按钮），<1280px 宽自动折叠。
- 窗口 min 1280×760，默认 1440×900；内容按视口高度自适应，默认零滚动条，过小窗口回退为面板内滚动。
- dev 预览：`frontend/preview.html`（mock IPC，浏览器直接验证布局，不入产物）。

## 3. 状态（Zustand，ADR-17）

```ts
interface WorkspaceState {
  backendStatus: 'starting'|'ready'|'error'
  activeDatasetId: string | null
  activeFrameIndex: number            // 切换数据集时归零，重启不恢复
  activeDescriptorRunId: string | null
  datasets: DatasetMeta[]
  jobsBadgeCount: number
}
```

- 持久化：`activeDatasetId` 存 backend `settings` 表（`workspace.activeDatasetId`），启动恢复；`activeDescriptorRunId` 同样持久化（`workspace.activeDescriptorRunId`，切换数据集时置空不写入）；`activeFrameIndex` 不恢复。
- Analysis 页视图（当前子页、投影方法、总览模块、粒度/预处理、着色）持久化为 `workspace.analysisUi`（JSON）；已算结果按「子页 + run + 参数组合」记槽位，持久化为 `workspace.analysisSlots`（JSON，最多 16 条）。切回子页恢复该页最近结果（参数精确匹配优先），改变方法/粒度等参数时自动切换到该参数组合的已算结果、无则清空待运行；参数控件的选项/标签前以绿点标注该参数组合已有缓存结果；重进页面或重启 APP 时按 analysis id 从后端工件（`analysis.preview` / `result.get_pca`）重新展示，不重算。
- 切换数据集 → 全部页面刷新（Overview 重新拉统计、Explore 重置帧、Descriptors 重算预检、Analysis 过滤）。

## 4. AntD tokens（logos.png 色板，融合 §124）

```ts
token: {
  colorPrimary: '#0F6CBD', colorInfo: '#0F6CBD',
  colorBgLayout: '#F5F6F8', colorBgContainer: '#FFFFFF',
  colorText: '#242424', colorTextSecondary: '#616161',
  colorBorder: '#E1E4E8', colorBorderSecondary: '#EAECF0',
  borderRadius: 6, fontSize: 14, controlHeight: 32,
  fontFamily: '"Segoe UI","Microsoft YaHei UI",sans-serif',
}
状态色：Success #107C10 / Warning #F0A000 / Danger #C42B1C / Neutral #8A8A8A
科学图表：连续数据用科学连续色图，分类数据用 categorical palette；元素着色用统一化学元素映射（Jmol 配色子集）。
```

数值排版：`font-variant-numeric: tabular-nums`；表格数值右对齐；单位用 Input suffix / 表格列头（§93/94）。图标：@fluentui/react-icons（Fluent System Icons），16/18px。

## 5. IPC client（frontend/src/ipc/client.ts）

```ts
class IpcClient {
  private nextId = 1
  private pending = new Map<number, {resolve, reject}>()
  async request<T>(method, params): Promise<T>   // 生成 id → backend_send(NDJSON) → pending
  onEvent(name, handler)                          // 订阅 job.progress / job.finished 等
}
```

- Tauri 事件 `backend-message`（原始 NDJSON 行）→ 解析 → id 命中 pending 则 resolve/reject，否则分发事件。
- `backend-exit` → backendStatus='error'，全局提示 + 重连按钮。
- 启动顺序：listen 就绪 → 收到 `backend.ready` → `system.info` 校验 → backendStatus='ready'。

## 6. 四基准页信息架构（M0 空壳 → 逐里程碑填充）

| 页面 | 内容 | 填充里程碑 |
|---|---|---|
| Overview | Summary 键值区（Structures/Atoms/Elements/Properties/Format/PBC/Created/File Size）+ Element Distribution 环形图 + 4 张直方图（E/atom、Force、Volume、Atoms/structure）+ Property Availability 矩阵（Virial 行，无 Magnetic Moment）；右侧常驻栏为 Data Health 面板（四项数据健康检查 + Scan status + Rescan） | M1（统计/直方图） |
| Explore | 帧导航条（i/N、prev/next/random/index）+ 3Dmol Viewer（70%）+ Structure Inspector（30%）+ Atom Table | M2 |
| Descriptors | registry 列表 + describe 信息 Inspector + schema 动态表单（8 类型 + 一层嵌套 object + model 两态）+ Execution（Device=CPU/Threads/Output/Scope）+ [Calculate] + input 兼容预检禁用 | M3（提交在 M4 走通计算） |
| Analysis | Run selector + Plotly Projection/Similarity/Clusters/Outliers/Sampling/Coverage/Compare tabs + shared Inspector + Open Explore | M5+ |

## 7. 视觉验收（§132 摘要）

当前 Dataset 一眼可见；主要科学内容占最大面积；Primary Action 唯一；数字对齐、单位规范；Failed 状态 = 图标 + 文字；空态简洁（"No datasets. Add a DeepMD or extxyz dataset to begin."）。
