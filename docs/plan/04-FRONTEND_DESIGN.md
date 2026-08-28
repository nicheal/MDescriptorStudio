# 04 · Frontend Design

> 状态：定稿（M0 入口条件）
> 依据：设计文档 §15/§64–133、UI.png、logos.png、ADR-6/7/17

## 1. 技术与目录

React 18 + TypeScript + Vite + AntD 5 + Zustand + ECharts + 3Dmol.js。

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
└── pages/  Overview.tsx Explore.tsx Descriptors.tsx Results.tsx Settings.tsx About.tsx
```

## 2. 布局骨架（ADR-6：无 Jobs Tab）

```text
┌──────────────────────────────────────────────────────────────┐
│ M Left: Sidebar 240px          Jobs② ⚙        (自定义标题栏区) │
├──────────────┬───────────────────────────────────────────────┤
│ DATASETS     │ ContextBar（数据集名 · 格式 · N structures ·   │
│ 搜索 + 列表   │ 元素 · PBC · E/F/V 徽标 · 路径）               │
│ + Add        ├───────────────────────────────────────────────┤
│              │ Tabs: Overview | Explore | Descriptors | Results│
│              ├───────────────────────────────────────────────┤
│              │ Main Workspace（页面内容 [+ Inspector 280px]）  │
├──────────────┴───────────────────────────────────────────────┤
│ StatusBar: ● Ready | MDescriptor 0.2.3 | CPU n threads | Mem  │
└──────────────────────────────────────────────────────────────┘
```

- Jobs 徽标 + Drawer（M4）：Drawer 展示运行中任务、进度条 4–6px、Cancel。
- Inspector 280px，可折叠（`>` 按钮），<1280px 宽自动折叠。
- 窗口 min 1280×760，默认 1440×900。

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

- 持久化：`activeDatasetId` 存 backend `settings` 表（`workspace.activeDatasetId`），启动恢复；`activeFrameIndex` 不恢复。
- 切换数据集 → 全部页面刷新（Overview 重新拉统计、Explore 重置帧、Descriptors 重算预检、Results 过滤）。

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
| Overview | Summary 键值区（Structures/Atoms/Elements/Properties/Format/PBC/Created/File Size）+ Element Distribution 环形图 + 4 张直方图（E/atom、Force、Volume、Atoms/structure）+ Property Availability 矩阵（Virial 行，无 Magnetic Moment）+ Quick Actions（Explore/Compute/Statistics 快捷导航）+ Recent Jobs | M1（统计/直方图）、M4（Recent Jobs） |
| Explore | 帧导航条（i/N、prev/next/random/index）+ 3Dmol Viewer（70%）+ Structure Inspector（30%）+ Atom Table | M2 |
| Descriptors | registry 列表 + describe 信息 Inspector + schema 动态表单（8 类型 + 一层嵌套 object + model 两态）+ Execution（Device=CPU/Threads/Output/Scope）+ [Calculate] + input 兼容预检禁用 | M3（提交在 M4 走通计算） |
| Results | run 列表 + PCA plot-first（75–80%）+ Heatmap（atom 级当前结构）+ Selected Sample Inspector + Open Explore | M5 |

## 7. 视觉验收（§132 摘要）

当前 Dataset 一眼可见；主要科学内容占最大面积；Primary Action 唯一；数字对齐、单位规范；Failed 状态 = 图标 + 文字；空态简洁（"No datasets. Add a DeepMD or extxyz dataset to begin."）。
