# GUI 英中双语版本实施计划

## 目标
在现有英文 GUI 基础上添加"设置 → 语言"切换（English / 简体中文），默认语言保持英文且英文文案逐字不变；新增完整中文版本，切换即时生效并持久化。

## 方案总览：轻量自研 i18n（不引入新依赖）

设计原则：**英文文案本身就是 key**。`t("Refresh")` 在英文模式下原样返回 key（英文零回归），中文模式下查 `zh.ts` 字典返回中文，找不到时回退英文。替换工作量约 18 个文件、300+ 条文案。

### 新增文件（frontend/src/i18n/）
1. **`index.ts`** — 核心模块：
   - zustand store `useI18n`：`lang: "en" | "zh"`、`setLang()`（同时写 localStorage 和后端 `settings.set`，key 为 `ui.language`）、启动时 `initLanguage()`（localStorage 同步初值 + 从后端 `settings.get` 读取覆盖，在 `backend.ready` 处理流程中、主界面渲染前完成，避免闪烁）
   - `useT()` hook（组件内使用，语言切换自动重渲染）；`t(key, vars?)` 支持 `{name}` 占位符插值
   - `getLocale()`：中文模式返回 `"zh-CN"`（用于日期本地化：今天/昨天、日期格式），英文模式返回 `undefined`（保持现状）
2. **`zh.ts`** — 中文词典（"对应中文版本"），覆盖全部界面文案
3. **`index.test.ts`** — 用正则扫描 src 下所有 `t("...")` 调用点，断言每个 key 都存在于 zh.ts（防止翻译遗漏的完整性测试）

### 设置界面（SettingsDrawer.tsx）
新增 "LANGUAGE / 语言" 分区（置于顶部），Radio 选项：`English` / `简体中文`（语言名用各自语言显示）。切换即写入持久化并全局生效。

### antd 组件库本地化
`main.tsx` 与 `preview.tsx` 增加一个订阅语言的小 Root 组件，向 `ConfigProvider` 传 `zhCN` / `enUS` locale（antd 默认即 en_US，英文模式行为不变；中文模式下表格分页、Empty、确认框等组件内置文案变中文）。

## 文案改造范围（全部 frontend/src）

| 文件 | 主要内容 |
|---|---|
| App.tsx | 页签（Overview→总览 等）、后端状态提示、引擎更新通知 |
| layout/TitleBar、Sidebar、ContextBar、StatusBar、RightRail | 数据集管理、右键菜单、数据健康面板、最近作业 |
| JobsDrawer、datasetActions、SettingsDrawer、SchemaForm、StructurePreview、Histogram | 作业列表/停止、重命名/删除确认、设置页、参数表单、查看器 |
| pages/Overview、Explore、Descriptors、DescriptorResults | 统计表、属性可用性、结构检查器、描述符配置三段表单、结果表 |
| pages/Analysis、analysisVisualizations | 页签、控件标签、分析历史、图表轴标题/悬停模板/图例/指标条（工作量最大，约 200 条） |
| stores/jobs.ts | `JOB_TYPE_LABEL` 改为经 `t()` 的函数（"描述符计算"等）；新增 `jobStatusLabel()`（QUEUED→排队中 等）供各渲染处使用 |

处理要点：
- **复数/动态串**：用两条 key 保持英文原样，如 `{n} job running` / `{n} jobs running`（中文同为"正在运行 {n} 个作业"）、`{n} dataset(s)` 同理；`{n} structures`→"{n} 个结构"。
- **图表文案**：Plotly/ECharts 的轴标题、悬停模板（`frame {i}`→"帧 {i}"、`count:`→"数量："）、图例、颜色条标题、类别名（Noise→噪声、Covered→已覆盖 等）全部纳入词典，渲染时取值，切语言即重绘。
- **既有中文图注**（Analysis 概览 6 条 ChartCaption）：zh 收录现有中文原文；英文模式显示新写的英文翻译（按你刚确认的选项）。
- **日期/时间**：`toLocaleString` 等传入 `getLocale()`；`formatRailTime` 的 Today/Yesterday → 今天/昨天。
- **不改**：品牌名 "MDescriptor Studio"、窗口标题、后端返回的错误消息（`code: message` 原样）、元素符号、`backend/` 与 `src-tauri/`（注意 `backend/.../analysis/engine.py` 有未提交改动，不碰）。

## 验证
1. `npm --prefix frontend run build`（tsc 严格类型检查，zh 字典类型安全）
2. `npm --prefix frontend run test`（vitest，含新增 i18n 完整性测试）
3. `npm --prefix frontend run e2e`（Playwright 全套 —— e2e 断言大量英文原文，是"英文版本不动"的端到端保障；另新增 1 条 e2e：设置中切到中文 → 断言中文文案出现 → 切回英文）