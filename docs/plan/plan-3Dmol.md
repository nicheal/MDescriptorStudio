# 3Dmol 界面支持直接点击选中原子

## 现状（实现前基线）
- 两处 3Dmol 查看器(`frontend/src/pages/Explore.tsx` 主查看器、`frontend/src/components/StructurePreview.tsx` Analysis 预览)都只做单向渲染,未注册任何点击事件;选中只能通过 Atom Table 行点击/图表点选完成。
- 选择状态存于 zustand `workspace.selectedSample`(`{ datasetId, mode, frame, atom? }`),Explore 的 Atom Table 行点击(Explore.tsx:832-851)已有完整的"点已选原子=取消"逻辑。
- 后端 `dataset.frame` 载荷的 XYZ 中,周期镜像原子(ghost)追加在真实原子之后,但 `periodic_boundary_ghosts`(dataset_service.py:803)丢弃了母原子索引 `src`,前端无法还原 ghost→母原子映射。
- 3Dmol 2.5.5 点击拾取 API 已从源码确认:`viewer.setClickable({}, true, cb)` 会给所有原子设 `clickable` 标记并构建拾取用 intersectionShape,**必须在 `render()` 之前调用**;mouseup 位移 <5px 且射线命中时回调 `cb(atom, viewer, event, container)`;`addAtoms` 用 `extend({}, olda)` 复制原子对象,自定义属性(如 `i`/`parent`)会原样保留并可从回调读回。注意 `createViewer` 配置里的 `callback` 是"viewer 创建完成"回调,与点击无关,必须用 `setClickable`。

## 改动清单

### 1. 后端:暴露 ghost→母原子映射
`backend/mdescriptor_studio_backend/services/dataset_service.py`
- `periodic_boundary_ghosts`(:803):返回值从 `(element, position)` 扩展为 `(element, position, parent_index)`——循环里已有 `src`(母原子下标,:859-861),补上 `out.append((symbols[i], p, int(i)))`。该函数仅此一处调用(:762)。
- 帧载荷构建(:784-800):新增 `"ghost_parents": [int(g[2]) for g in ghosts]`(无 ghost 时为空列表)。
- `tests/test_ghost_images.py`(必要时含 `test_dataset_flow.py`):补充断言——`ghost_parents` 长度 == `ghost_count`、每个值都是合法真实原子下标、位置与母原子差一个晶格矢量。

### 2. 前端协议类型
`frontend/src/types/protocol.ts` `FramePayload`(:145):新增可选字段 `ghost_parents?: number[]`(与 XYZ 中追加的周期镜像一一对应的母原子下标；局部壳层 viewer 可消费)。

### 3. Explore 主查看器(核心)
`frontend/src/pages/Explore.tsx`
- `ViewerAtom`(:45)增加 `i: number`(viewer 模型索引)，真实原子与 Atom Table 行保持一致；周期镜像另带 `parent?: number` 指向母原子。
- `parseViewerAtoms(frame, cutoff)` 扩展为 `parseViewerAtoms(frame: FramePayload, cutoff, includePeriodicImages?)`：普通 viewer 只从真实 `atom_rows` 构建；局部壳层开启时才从 `xyz` 追加所需周期镜像，并通过 `ghost_parents` 保留镜像到母原子的映射。
- 局部壳层开启时，`dataset.frame` 的请求阈值取 `max(bondCutoff, localCutoff)`；前端将返回帧上的 `bond_cutoff` 保持为用户的成键显示阈值，避免扩大周期 padding 后改变普通成键显示。
- viewer ref 内联类型(:145-159)增加 `setClickable(sel, clickable, callback)`。
- 把 Atom Table 行点击逻辑(:832-851)抽成共享的 `selectAtom(atomIndex)`(相等则 `setSelectedSample(null)` 取消,否则 `setShowDistancePair(false)` + `setSelectedSample({..., mode: "atom", frame: idx, atom})`),表格 onRow 与 viewer 点击共用。
- 渲染 effect(:398-529)中,在高亮/晶胞绘制之后、`v.render()` 之前:
  `v.setClickable({}, true, (atom) => { const target = atom.parent ?? atom.i; if (合法) clickRef.current?.(target); })`。
  用 `clickRef = useRef` 持有最新 handler,避免闭包过期。局部壳层(shell)模式下 `renderAtoms` 经 `{...atom}` 展开,`i`/`parent` 自然保留,点击在重映射索引下依然正确；选中行由容器滚动到中央。

### 4. StructurePreview
`frontend/src/components/StructurePreview.tsx`
- 同样的 `ViewerAtom`/`Viewer` 类型扩展；普通 `parseViewerAtoms(frame)` 只使用 `frame.atom_rows` 的真实原子，局部壳层预览才纳入 `xyz` 中的周期镜像并将点击映射回母原子。
- 新增可选 prop `onSelectAtom?: (atom: number) => void`;渲染 effect 内 `viewer.setClickable({}, true, ...)` 并经 ref 转发,避免 props 闭包过期。

### 5. Analysis 页接线
`frontend/src/pages/Analysis.tsx`(:1139)
- 给 `StructurePreview` 传 `onSelectAtom`:
  - 点已选原子(`selectedPoint?.row === atom`)→ 取消:`setInspectedPoint(null)`、`setSelectedIndices([])`、`updateCachedSelection([])`、`setSelectedSample(null)`。
  - 否则:若 `points` 中存在 `frame === selectedFrame.index && row === atom` 的点,直接 `handlePoint(existing)`(复用图表选中/缓存/inspector 全链路);否则仿照 ResultPanel onSelect(:1131)合成 Point(`frame: selectedFrame.index, row: atom, x: 0, y: 0`)后走 `handlePoint`,保证预览高亮、Inspector、store 三者一致。

### 6. 开发 Mock
`frontend/src/preview.tsx`(:637-683):mock 帧生成器按母原子循环生成 ghost,顺带输出 `ghost_parents`,保证开发预览模式下点击行为一致(字段可选,不阻塞)。

### 7. 文档(轻量)
- `docs/plan/04-FRONTEND_DESIGN.md` Explore 一节补一句"viewer 支持点击选中原子"。
- `docs/plan/03-BACKEND_DESIGN.md` 帧载荷说明补 `ghost_parents` 字段。

## 不做的事
- 不加 hover 高亮/悬浮提示(3Dmol hoverable 会显著增加重绘成本)。
- 不改高亮配色(保持现有红色 #D13438 选中样式,与 Atom Table 一致)。
- 空白处点击不取消选中(3Dmol 仅在命中原子时回调,保持行为简单,取消仍走再次点击)。

## 最新显示与交互要求
- 3Dmol 主查看器与 Analysis 结构预览默认只渲染晶胞内的 `natoms` 个真实原子；开启局部壳层时，按壳层半径临时纳入必要的周期镜像，壳层之外的晶胞外重复原子仍不显示。
- Explore 中点击 viewer 原子后，Atom Table 自动将对应行滚动到可视区域中央并保持红色高亮；viewer 点击与 Atom Table 行点击共用同一选择/取消逻辑。
- 最短距离指标与高亮只使用真实原子索引；局部壳层的邻居计算则可使用周期镜像，并把镜像点击归一到真实原子索引。

## 验证
1. 后端:`pytest tests/test_ghost_images.py tests/test_dataset_flow.py`。
2. 前端:`npm run build`(tsc)+ `npx vitest run`。
3. 手动核对(真机运行):Explore 默认不显示晶胞外周期重复原子；开启局部壳层后，跨晶胞邻居能出现在壳层中且镜像点击选中原胞对应行，壳层外重复原子不显示；点击 viewer 原子后对应表格行滚动到中央并高亮、再点取消；最短距离仍只使用真实原子；Analysis 局部壳层预览点击镜像后 Inspector/图表选中联动、再点取消。
