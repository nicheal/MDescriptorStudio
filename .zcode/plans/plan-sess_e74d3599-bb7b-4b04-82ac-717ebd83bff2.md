# 3Dmol 界面支持直接点击选中原子

## 现状
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
`frontend/src/types/protocol.ts` `FramePayload`(:145):新增可选字段 `ghost_parents?: number[]`(注释:与 XYZ 中追加的周期镜像一一对应的母原子下标)。

### 3. Explore 主查看器(核心)
`frontend/src/pages/Explore.tsx`
- `ViewerAtom`(:45)增加 `i: number`(XYZ 载荷内索引,含 ghost)与 `parent?: number`。
- `parseViewerAtoms(xyz, cutoff)` 改签名为 `parseViewerAtoms(frame: FramePayload, cutoff)`:给每个原子挂 `i`;`index >= natoms` 的 ghost 挂 `parent: frame.ghost_parents?.[index - natoms]`。同步更新 :174 与 :406 两处调用。
- viewer ref 内联类型(:145-159)增加 `setClickable(sel, clickable, callback)`。
- 把 Atom Table 行点击逻辑(:832-851)抽成共享的 `selectAtom(atomIndex)`(相等则 `setSelectedSample(null)` 取消,否则 `setShowDistancePair(false)` + `setSelectedSample({..., mode: "atom", frame: idx, atom})`),表格 onRow 与 viewer 点击共用。
- 渲染 effect(:398-529)中,在高亮/晶胞绘制之后、`v.render()` 之前:
  `v.setClickable({}, true, (atom) => { const target = atom.parent ?? atom.i; if (合法) clickRef.current?.(target); })`。
  用 `clickRef = useRef` 持有最新 handler,避免闭包过期。局部壳层(shell)模式下 `renderAtoms` 经 `{...atom}` 展开,`i`/`parent` 自然保留,点击在重映射索引下依然正确。

### 4. StructurePreview
`frontend/src/components/StructurePreview.tsx`
- 同样的 `ViewerAtom`/`Viewer` 类型扩展(它的 `parseViewerAtoms(frame)` 已接收 frame,直接从 `frame.ghost_parents` 取)。
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

## 验证
1. 后端:`pytest tests/test_ghost_images.py tests/test_dataset_flow.py`。
2. 前端:`npm run build`(tsc)+ `npx vitest run`。
3. 手动核对(真机运行):Explore 点击原子=表格行选中效果一致、再点取消、局部壳层开启时点击邻居/gohst 正常;Analysis 预览点击原子后 Inspector/图表选中联动、再点取消;周期体系点击边界外镜像原子选中其母原子。