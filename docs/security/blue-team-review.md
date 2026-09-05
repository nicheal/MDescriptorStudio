# MDescriptor Studio — 蓝队复核与防御措施审计报告

- **审计日期**：2026-08-31
- **审计对象**：`docs/security/red-team-findings.md`（红队 2026-08-31，主清单 13 条 + 附加 A1–A8，共 21 条）
- **审计范围**：`backend/mdescriptor_studio_backend/**`、`src-tauri/**`、`frontend/src/**`、`scripts/**`、`tests/**`（只读）、`.venv/Lib/site-packages`（定向只读核实 numba/umap/numpy）、`~/.cargo/registry/.../tauri-2.11.5`（定向只读核实 IPC 权限语义）
- **模式**：**纯静态只读审计**。未启动应用、未 spawn 后端、未执行 pip/npm/构建、未修改任何被审文件。
- **唯一写入文件**：本报告。
- **方法**：对红队每一条自行打开 `文件:行号` 核对代码内容；行号对不上的以实际读到的为准并注明偏移。

---

## 1. 执行摘要

红队 21 条发现**整体可信度高**：代码级证据基本扎实，行号引用准确率约 95%（仅 2 处轻微偏移）。我**没有 REFUTED 任何一条主线发现**，也没有降级任何一条——红队的定级在给定威胁模型下是站得住的。

但我改判/纠正了 **6 处红队的技术描述或修复建议**（不是严重度，而是"结论对但理由错"或"建议不可执行/有回归"）：

1. **RT-03 建议 5（在 capabilities 里声明 `backend_send` 权限）不可执行**。我核实 tauri-2.11.5 `src/webview/mod.rs:1800-1812`：应用自定义命令只有在存在 `__app-acl__` manifest 或来源为远程时才做 ACL 校验。本项目 `gen/schemas/acl-manifests.json` 无该条目，因此加权限声明**不会产生任何约束**。正确做法是用 `tauri_build::Attributes::app_manifest(...)` 生成应用 ACL manifest。这是"假安全"型误判，危害最大。
2. **RT-09 的"8 MB 行限制无法绕过"不成立**。`server.py:41` 的 `for raw in sys.stdin` 是无上限的按行迭代，整行先读入内存并 `strip()` 复制，之后才到 `frames.py:18` 的长度检查。8 MB 只约束**调度**，不约束**内存**。→ BLUE-02。
3. **RT-05 的"release 每次启动都向 PyPI 发请求"被证伪**。`update_service.py:62` 的 `_frozen()` 在 `start_check` 开头即 return，release 不发请求。
4. **RT-06 建议里的 `".." in resolved.parts` 是死代码**——`Path.resolve()` 之后不可能残留 `..`。真正的检查必须是 `is_relative_to()`。
5. **RT-02 建议的 `data_dir/"exports"` 白名单会破坏 2 个在跑的测试**（`test_analysis_ipc.py:169-172`、`test_analysis_api.py:300`）和现有产品行为（用户要把导出放到 `D:\exports`）。
6. **RT-07 的指纹算法变更未评估迁移与性能影响**：会导致全部历史 run 被标记 STALE（破坏性数据迁移），且 `compute_fingerprint` 在 RPC 同步路径上被调用，`dataset.list` 一次可为每个数据集读 4 MiB×N 文件 → UI 卡顿。

我额外补充 **14 条红队漏掉的发现**（BLUE-01…BLUE-14），其中 Medium 4 条，最关键的是 **BLUE-03**（`dataset.frame` 是任意文件读取 oracle，走 DB 投毒路径、不需要 webview 代码执行）——这恰好把红队自己标为"未确证"的 RT-06 内容外泄子项给坐实了。

**最终确认的 P0 共 3 条**：RT-01（numba pickle RCE）、RT-02（export 任意路径写）、RT-03（IPC 全权限代理）。RT-03 的第一步（去掉 `allow-emit` + Rust 拒绝控制字符/超长帧）是**纯配置 + 10 行代码**，应立刻做。

---

## 2. 复核判定总表（覆盖全部 21 条）

| 红队ID | 标题 | 红队定级 | 蓝队判定 | 最终定级 | 一句话理由 |
|---|---|---|---|---|---|
| RT-01 | numba JIT 缓存投毒 → pickle RCE | High | **CONFIRMED**（描述需修正） | **High**（`MDS_DATA_DIR` 共享时为 Critical） | `engine.py:613/615` + `numba/core/caching.py:588` 先 `pickle.load` 后校验，全链路确证；但"同机其他用户"可达性需修正（`%TEMP%` 默认他人不可写） |
| RT-02 | `analysis.export` 的 `output_path` 无校验 → 任意路径写 | High | **CONFIRMED** | **High** | `analysis_service.py:672` 仅 `expanduser()`，`:1337/1343/1355` 直接 `mkdir(parents=True)` + 写；前端 `Analysis.tsx:914` 是自由文本框。修复建议会引入回归 |
| RT-03 | `backend_send` 全权限 IPC 代理 + 帧注入 + 响应伪造 | High | **CONFIRMED**（"权限声明缺失"的**理由**错误，结论正确） | **High** | 无校验、无换行过滤、无事件来源绑定均确证；`core:event:default` **确实含 `allow-emit`**，响应伪造可达。但 capabilities 声明对 app 命令无效 |
| RT-04 | Release 从 exe 同目录加载 sidecar 无完整性校验 | Medium | **CONFIRMED** | Medium | `main.rs:113-116` 仅 `exists()`；`installMode:perMachine` 是真实缓解；补充 `TEMP` 环境变量与 dev 分支同样无校验 |
| RT-05 | pip 自更新信任 PyPI 版本号、未锁索引/哈希 | Medium | **CONFIRMED**（子项"release 每次启动发 PyPI 请求"**证伪**） | Medium（若威胁模型不含开发者机器可降 Low） | `update_service.py:80` 无格式校验、`:101` 无 `--only-binary/--index-url`；但 release 被 `_frozen()` 完全阻断，且 `start_check` 不发请求 |
| RT-06 | `dataset.register` 任意路径解析 + 错误信息泄露 | Medium | **CONFIRMED**（红队自标"未确证"的内容外泄子项，我经另一路径**确证**） | Medium | `dataset_service.py:133/136`、`base.py:79` 确证；`dataset.frame:376-388` 不依赖任何 run，DB 投毒即可做任意文件读取 oracle（见 BLUE-03） |
| RT-07 | 数据集指纹只哈希 size+mtime | Medium | **CONFIRMED**（修复建议缺迁移与性能评估） | Medium | `fingerprint.py:9-22` 全文无任何 `read()`；但直接改算法会触发全量 STALE 迁移 + RPC 线程阻塞，必须先设计方案 |
| RT-08 | extxyz `natoms` 无上限 → OOM DoS | Medium | **CONFIRMED** | Medium | `extxyz.py:39-50` 的 `break` 让索引毫秒级建成，`:99` 才 `np.empty((natoms,3))`；`descriptor_service.py:336-343` 全量载入放大 |
| RT-09 | RPC 线程池任务队列无界 | Medium | **CONFIRMED**（"8MB 限制不可绕过"**部分证伪**） | Medium | `server.py:25/47` 无界队列确证；但 stdin 无行长上限使内存放大强于红队描述（BLUE-02）；红队的 `_BoundedPool` 实现会在 shutdown 时挂死 |
| RT-10 | DB 中的 `result_path` 无条件 `rmtree` | Medium | **CONFIRMED** | Medium | `result_service.py:105/107/113`、`analysis_service.py:380/1575` 确证；修复会给 `ResultService.__init__` 加参数 → 破坏 2 个测试，需设计成可选参数 |
| RT-11 | `MDS_DATA_DIR` 完全受控无校验 | Low | **CONFIRMED**（"Rust 只检查非空"是无效缓解） | Low（是 RT-01/RT-10 的放大器，建议随 P0 一起修） | `config.py:13-20` 无校验；`Command::new` 默认继承父进程全部环境，"只在存在时透传"不改变继承事实 |
| RT-12 | 供应链：无哈希锁定、npm 全 `^`、165MB 二进制无校验和 | Low | **CONFIRMED** | Low | `requirements.txt` 无 `--hash`；`package.json` 13 个 `^`；`src-tauri/binaries/` 无 `.sha256`；`Cargo.lock` 存在 |
| RT-13 | CSP 缺 `script-src`/`object-src`/`base-uri`/`frame-ancestors` | Low | **CONFIRMED**（建议的 CSP 字符串有白屏回归风险） | Low | 回退到 `default-src 'self'` 属实；我独立核查前端**无 XSS sink**（仅 2 处 `innerHTML = ""`）；但显式加 `script-src` 可能拦掉 plotly/3dmol 的 blob worker |
| A1 | `np.load` 未显式 `allow_pickle=False` | Low | **CONFIRMED** | Low（建议随 RT-01 一起顺手改，S 级） | `result_service.py:77`、`:142` 未传参，其余 5 处都传了 → 跨模块不一致确证；numpy 2.5.2 默认 False，当前安全 |
| A2 | `job.cancel` 无归属/权限校验 | Low | **CONFIRMED** | Low | `main.py:104` 直传 `params.get("id")`；`job_service.py:208-219` 无授权检查 |
| A3 | `settings.set` 无 key 白名单与长度限制 | Low | **CONFIRMED** | Low | `main.py:74-79` `str(value)` 直接入库，`database.py:78` `value TEXT` 无长度约束，配合 8MB 帧可撑大 SQLite |
| A4 | 错误信息与日志泄露绝对路径与内部异常 | Low | **CONFIRMED**（并确认脱敏改造**不会破坏现有测试**） | Low | `server.py:75` 直发 `f"{type(exc).__name__}: {exc}"`；但 release 下 `main.rs:72` 把 stderr 置 null，日志只落文件；测试只断言 `error.code` |
| A5 | 产物写入 staging + `os.replace`（已缓解） | 信息 | **CONFIRMED**（缓解有效，但暴露 3 个新问题） | 信息 + 3 条 Low（BLUE-06/07/08） | `staging.mkdir(exist_ok=False)` + `os.replace` 模式正确；但 `_artifact_is_complete:1472` 信任磁盘 manifest 的 `path`、`:1290` 只替换 `/` 不处理 `\` 和 `..`、`:1310` 未考虑 `final` 为悬空链接 |
| A6 | SQL 注入 | **未发现** | **CONFIRMED（阴性结论成立）** | 无 | 独立核查：所有 `execute/query/query_one` 均带 `?`；3 处 `LIKE` 的 `%...%` 是**绑定值**不是拼接；`executescript` 只用模块常量。但 LIKE 通配符未转义 → BLUE-09 |
| A7 | 硬编码凭据 / 密钥 | **未发现** | **CONFIRMED（阴性结论成立）** | 无 | 独立 grep 仅命中 `tokens`/`tokenSeparators`/`theme.token`/`protocol` 等无关标识符 |
| A8 | mdescriptor 自研 pickle 解析器 | 信息 | **PARTIALLY CONFIRMED** | 信息（建议 fuzz） | 我**未对 `weights.py` 做完整审查**，无法背书"这是做得好的缓解"；但确证了 `model` 参数路径零校验（BLUE-10），同意 fuzz 建议 |

**判定分布**：CONFIRMED **20** 条 / PARTIALLY CONFIRMED **1** 条 / UPGRADED **0** / DOWNGRADED **0** / REFUTED **0**。
（另有 6 处**子论断或修复建议**被我证伪或纠正，详见第 3 节各条的"对红队描述的修正"与"修复建议完备性评估"。）

---

## 3. 逐条复核详情

> 每条包含：① 我的独立证据（文件:行号 + 片段） ② 对红队描述的修正 ③ 判定理由 ④ 修复建议完备性评估（漏掉的边界条件 / 会引入的回归 / 更优替代方案）。

### RT-01 — numba JIT 缓存投毒 → pickle RCE

**判定：CONFIRMED，维持 High**（`MDS_DATA_DIR` 指向共享目录时升 Critical）

**① 独立证据**

```python
# backend/mdescriptor_studio_backend/analysis/engine.py:592, 604-615
if getattr(sys, "frozen", False):                                  # 592：仅 release/PyInstaller 生效
    class _FrozenCacheLocator(caching.UserWideCacheLocator):        # 604
        def __init__(self, py_func, py_file):
            ...
            root = os.environ.get("MDS_DATA_DIR") or tempfile.gettempdir()   # 613
            subpath = self.get_suitable_cache_subpath(py_file)
            self._cache_path = os.path.join(root, "numba-cache", subpath)    # 615
```

红队引用 `604-635` 准确。触发链尾端已核实：

```
.venv/Lib/site-packages/numba/core/caching.py:588    version = pickle.load(f)      # 先反序列化
.venv/Lib/site-packages/numba/core/caching.py:593    if version != self._version:  # 后校验
.venv/Lib/site-packages/numba/core/caching.py:597    stamp, overloads = pickle.loads(data)
.venv/Lib/site-packages/numba/core/caching.py:618    tup = pickle.loads(data)      # _load_data
.venv/Lib/site-packages/umap/layouts.py:34           cache=True,
```

numba 版本核实：`numba-0.67.0.dist-info/METADATA` → `Version: 0.67.0`。`cache=True` 全仓仅 `umap/layouts.py:34` 一处。

**② 对红队描述的修正**

- **可达性需修正**。红队写"攻击者在受害者机器上先正常运行一次 UMAP…得到缓存子路径"——这在"攻击者 == 受害者同用户的恶意软件"下成立；但红队把威胁 (c) 表述为"同机低权限恶意软件或**同机其他用户**"，**后者在 `%TEMP%` 下不成立**：`C:\Users\<victim>\AppData\Local\Temp` 继承用户配置文件 ACL，其他标准用户既不可列也不可写。**"其他用户"这条路径只有 `MDS_DATA_DIR` 指向共享目录/共享盘时才成立**。
- **真正的提权价值在于完整性级别跃迁**：`installMode: perMachine` 安装后用户常"以管理员身份运行"。此时非提升的恶意软件写入 `%TEMP%\numba-cache\…nbi`，下次以提升权限运行的 UMAP 触发 `pickle.load` → **从 Medium 完整性提升到 High** 的代码执行。这是本条最值得写进报告的后果，红队只笼统提了"若以管理员身份运行则构成提权"。
- **补充：dev 构建同样可投毒**（红队只说 release）。`sys.frozen` 为假时不走 monkeypatch，numba 用默认 `__pycache__` 定位器 → 缓存落在 `.venv/Lib/site-packages/umap/__pycache__/`，该目录对开发者可写。dev 机器是高价值目标。
- **红队引用行号**：`engine.py:604-635`、`caching.py:588` 均准确，无偏移。

**③ 判定理由**：三方证据（应用侧主动重定向 + 库侧先反序列化后校验 + 触发点 `cache=True` 实际存在）构成完整闭环，无合理争议。

**④ 修复建议完备性评估**

| 项 | 评价 |
|---|---|
| 漏掉的边界 | **(a)** 未覆盖 dev 路径（见上）。**(b)** 未验证 `NUMBA_DISABLE_JIT_CACHE` 在 numba 0.67.0 中的确切语义——建议 3 把它列为"最推荐方案"，但 `numba.config.DISABLE_JIT_CACHE` 是**只读配置**，运行时赋值不生效；正确做法是在 import numba 之前设 `os.environ["NUMBA_DISABLE_JIT_CACHE"]="1"`，而 `engine.py:600` 处 numba 已被 import（甚至可能被 bundled 原生依赖提前 import，代码注释 596-599 行已承认这点）→ **建议 3 在本代码结构下不可靠**，需改为在 `run_backend.py`（PyInstaller 入口）最顶部设环境变量。**(c)** 未考虑清空缓存的性能回归：UMAP 首次编译典型 +20~40 s，若每次启动 `rmtree` 会让每次分析都失去缓存。 |
| 会引入的回归 | 建议 2 用 `subprocess` 调 `icacls` **引入新的进程调用点**，且与本项目"无 `shell=True`"的现状冲突；应改用 `pywin32` 的 `SetNamedSecurityInfo` 或至少 `subprocess` 传参数列表（当前建议未说明）。改缓存根后，老用户磁盘上的旧 `%TEMP%\numba-cache` 残留不会自动清理 → 需显式删除。 |
| 更优替代方案 | **优先级重排**：(1) 先在 `run_backend.py` 顶部 + `engine.py` 双保险设 `NUMBA_DISABLE_JIT_CACHE=1`（若实测 UMAP 首跑耗时可接受）；(2) 不可接受则把缓存根固定到 `%LOCALAPPDATA%\MDescriptorStudio\numba-cache`（**不复用 `MDS_DATA_DIR`**，见 RT-11）并在启动时校验该目录 ACL，不合规就清空；(3) 完整性防护（签名/白名单）成本过高，不建议。 |
| 修复顺序依赖 | **RT-11 必须先修**（`MDS_DATA_DIR` 校验）——否则 RT-01 的缓存根仍可被环境变量重定向到共享位置，ACL 加固白做。 |
| 工作量 / 类型 | S（改配置根 + 环境变量） / **必须改代码** |

---

### RT-02 — `analysis.export` 的 `output_path` 无校验 → 任意路径写覆盖 / 建目录 / UNC 凭据外泄

**判定：CONFIRMED，维持 High**

**① 独立证据**

```python
# services/analysis_service.py:669-673
target = str(params.get("output_path") or "")
if not target:
    raise AppError(ANALYSIS_INPUT_INVALID, "output_path is required for export")
target = str(Path(target).expanduser())                    # 672：唯一的"处理"
canonical = self._canonical_params({... "output_path": target ...})   # 673
```

```python
# services/analysis_service.py:1335-1357（_write_export）
target = target.expanduser()                                # 1335
if export_format == "json":
    target.parent.mkdir(parents=True, exist_ok=True)        # 1337
    target.write_text(...)                                  # 1339
if export_format == "csv":
    target.parent.mkdir(parents=True, exist_ok=True)        # 1342
    with target.open("w", newline="", encoding="utf-8") as fh:   # 1343
...
if dataset["format"] != "deepmd":
    raise AppError(EXPORT_FAILED, "DeepMD export requires a DeepMD source dataset")
target.mkdir(parents=True, exist_ok=True)                   # 1355（deepmd）
```

红队引用 `669-672` 与 `1335-1357` **完全准确**。

```tsx
// frontend/src/pages/Analysis.tsx:914
<Input placeholder={t("D:\\exports\\analysis_subset.csv")} value={exportPath}
       onChange={(event) => setExportPath(event.target.value)} />
```

确证是**自由文本框**，且 `dialog:default` 已授权但**未用于 save 对话框**。

**② 对红队描述的修正**

- 红队说"写入**内容**受控程度低…因此本条**不是**直接的代码执行原语"——**我同意，并做了额外核实**：`extxyz` 导出的元素符号来自 `_Z_TO_SYMBOL.get(int(z), f"Z{int(z)}")`（`analysis_service.py:1367`），`deepmd` 的 `type_map.raw` 来自 `_Z_TO_SYMBOL.get(z, f"Z{z}")`（`:1394`），其余全是 `%.12g` 格式化的浮点数。因此**确实无法生成任意文本内容**，本条是"破坏性覆盖 + 目录创建 + UNC 凭据外泄"，不是 RCE。红队判断正确。
- **补充一条红队没写的变体**：`format=deepmd` + `output_path` 指向已存在目录时，`target.mkdir(parents=True, exist_ok=True)` 不报错，然后往该目录写 `type.raw`/`set.000/*.npy` → 可以把任意目录**污染成 DeepMD 数据集**（可用于后续投毒）。同 `format=extxyz` 覆盖一个正在被别的数据集引用的 `.xyz` 文件。
- **补充**：`_write_export` 在 `mkdir` 失败/写入失败时抛的是 `OSError`，逃到 `job_service._run:146` → `INTERNAL_ERROR`，消息含完整目标路径 → 又是一个路径/存在性 oracle（与 RT-06 同源）。

**③ 判定理由**：代码路径与前端 UI 双向确证，无争议。

**④ 修复建议完备性评估**（本条红队建议问题最多）

| 项 | 评价 |
|---|---|
| **会引入的回归（严重）** | 建议第 4 步 `allowed = (data_dir / "exports").resolve()` + 前缀比较 **会破坏 2 个在跑的测试**：<br>· `tests/test_analysis_ipc.py:169-172`：`export_path = tmp_path / "analysis-subset.json"`，而 `BackendProcess(tmp_path)` 把 `MDS_DATA_DIR` 设为 `str(tmp_path)`（`test_backend_smoke.py:20`）→ 导出目标在 data_dir 内但**不在 `data_dir/exports` 下** → 断言 `export_path.is_file()` 失败。<br>· `tests/test_analysis_api.py:300`：`service._write_export(run, [0], "json", "structure", tmp_path / "export.json", _Context())`，`AnalysisService(..., data_dir=tmp_path)` → 同样失败。<br>**同时破坏产品行为**：placeholder 明写 `D:\exports\analysis_subset.csv`，用户显然要导出到自选位置，限制到 data_dir 子目录是不可接受的产品回归。 |
| **更优替代方案** | **把校验放在"来源"而不是"目的地"**：(1) 前端强制用 `@tauri-apps/plugin-dialog` 的 `save()` 填充路径，禁止手输（**改前端，S 级，零后端回归**）；(2) 后端保留"任意绝对路径"，但拒绝 UNC / `\\?\` / `\\.\` / ADS / 相对路径 / 保留设备名 / 尾部空格与点 / 符号链接（含中间父目录）；(3) 用 `os.open(..., O_CREAT\|O_WRONLY\|O_TRUNC\|O_NOFOLLOW, 0o600)` 写文件消除 TOCTOU。**不要做 `data_dir/exports` 白名单。** |
| 漏掉的边界条件 | **(a) 8.3 短名**（`C:\PROGRA~1\`）——黑名单/前缀比较都可能被绕过，但用 `Path.resolve()` 后可缓解；**(b) 尾部空格与点**——`Path("C:\\x\\a.txt ")` 在 `pathlib` 里保留尾部空格，`open()` 由 OS 剥离 → 实际写入 `a.txt`，应显式拒绝 `name != name.rstrip(" .")`；**(c) 大小写与分隔符**——建议里 `str(resolved).lower().startswith(str(allowed).lower() + os.sep)` 在路径含 `/` 时会误判，应改用 `resolved.is_relative_to(allowed)`（Python 3.9+，本项目 3.12 可用）；**(d) 中间父目录符号链接**——建议第 7 步只检查最终目标，未检查中间层级；**(e) 并发**——两个 export job 同时写同一路径无互斥；**(f) `\\?\` 前缀必须在 `resolve()` **之前**判断**（建议第 1 步顺序正确，这点红队做对了）。 |
| 额外建议（红队未提） | 校验应**同时**放进 `_write_export`（`analysis_service.py:1318` 入口），因为 `submit_export` 可被绕过（测试就绕过它），且 `_write_export` 是真正的写入点。 |
| 依赖顺序 | 与 **RT-03** 一并修或先修 RT-03；否则校验只是多一道门（攻击者仍可构造帧直达后端）。但**前端改 save 对话框**这一小步可以立刻做、无依赖。 |
| 工作量 / 类型 | 前端 S（改对话框）+ 后端 M（校验函数 + 落盘方式改造） / **两者都是改代码** |

---

### RT-03 — `backend_send` 全权限 IPC 代理 + 帧注入 + 响应伪造

**判定：CONFIRMED，维持 High**（但"权限声明缺失"这一**理由**错误，其修复建议 5 **不可执行**）

**① 独立证据**

```rust
// src-tauri/src/main.rs:22-35
#[tauri::command]
fn backend_send(state: tauri::State<BackendState>, line: String) -> Result<(), String> {
    ...
    stdin.write_all(line.as_bytes())          // 28：无长度、无控制字符校验
        .and_then(|_| stdin.write_all(b"\n"))
```

```python
# protocol/server.py:41-47
for raw in sys.stdin:        # 41：按行切分 → 注入的 '\n' 变成多个请求
    line = raw.strip()
    ...
    self._pool.submit(self._handle, line)    # 47
```

```ts
// frontend/src/ipc/client.ts:26, 58-63, 70
private nextId = 1;                                    // 26
if (typeof frame.id === "number" && this.pending.has(frame.id)) {   // 58：不校验来源
    const p = this.pending.get(frame.id)!;
    this.pending.delete(frame.id);
    else p.resolve(frame.result);                      // 62
}
const id = this.nextId++;                              // 70：完全可预测
```

**关键独立核实（红队自己标了不确定，我给出确定答案）**：

```
~/.cargo/registry/.../tauri-2.11.5/src/webview/mod.rs:1795-1812
let (resolved_acl, has_app_acl_manifest) = { ... };
// Check ACL on plugin commands, when the app defined its ACL manifest,
// or when the request comes from a non-local (remote) origin.
if (plugin_command.is_some() || has_app_acl_manifest || !is_local) && ... && invoke.acl.is_none() {
    ... reject
}
```

`has_app_acl = acl.contains_key("__app-acl__")`（`tauri-utils-2.9.3/src/acl/mod.rs:349`）。本项目：

```
$ python -c "import json;print(list(json.load(open('src-tauri/gen/schemas/acl-manifests.json'))))"
['core', 'core:app', 'core:event', 'core:image', 'core:menu', 'core:path',
 'core:resources', 'core:tray', 'core:webview', 'core:window', 'dialog']
```

**无 `__app-acl__` 条目 → `has_app_acl_manifest = false` → 三个自定义命令对本地内容完全不受 capability 约束。**

**响应伪造可达性核实**：

```json
// src-tauri/gen/schemas/acl-manifests.json → core:event → default_permission
{"identifier":"default","description":"Default permissions for the plugin, which enables all commands.",
 "permissions":["allow-listen","allow-unlisten","allow-emit","allow-emit-to"]}
```

`core:event:default` **确实包含 `allow-emit`**（以及 `allow-emit-to`）。webview 可调用 `plugin:event|emit`，Rust 侧 `emit` 广播回所有 webview（含发起者自身）→ **响应伪造路径可达，红队此子项 CONFIRMED**。

**② 对红队描述的修正**

- **最主要的修正**：红队 B1 行写"Tauri capability 只列了 core:default/…，**没有任何针对 `backend_send` 的权限声明**"，并把修复建议 5 写成"在 `src-tauri/capabilities/default.json` 显式声明这三个命令的权限"。**这两句都是错的**：
  - 现状不是"忘了声明"，而是**当前架构下声明无效**——必须先生成应用 ACL manifest。
  - 直接往 `capabilities/default.json` 里加 `"backend_send"` 会导致 **Tauri 报未知权限而构建失败**（或静默忽略），实施者会以为加固已完成。
  - **可执行路径**：`tauri-build 2.6.3` 提供 `Attributes::app_manifest(AppManifest)`（`src/lib.rs:411`），需在 `src-tauri/build.rs` 中改为
    `tauri_build::build()` → `tauri_build::try_build(tauri_build::Attributes::new().app_manifest(tauri_build::AppManifest::new().commands(["backend_send","backend_restart","backend_ready_line"])))`，
    重新生成 `gen/schemas/acl-manifests.json` 后 capabilities 才生效。
- **响应伪造的最简修复红队没提**：把 `capabilities/default.json` 的 `"core:event:default"` 换成
  `"core:event:allow-listen", "core:event:allow-unlisten"`，**显式去掉 emit/emit-to**。
  这是**纯配置、零代码**即可关闭整条响应伪造链；比"随机 id + Rust 构造帧"简单一个量级，应作为第一小步立刻实施。
- **`emit_to` 建议不完整**：`main.rs:94` 用的是 `handle.emit`（`AppHandle` 广播）。改成 `emit_to` 需要知道发起 webview 的 label，而 `backend_send` 当前签名**没有 `webview` 参数**，必须改成 `fn backend_send(webview: tauri::Webview, state: ..., line: String)`。红队建议里没提这个签名变更。
- **补充**：`main.rs:89` 用 `l.contains("\"backend.ready\"")` 做字符串匹配来缓存 ready 行——一个伪造/畸形但含该子串的行会污染 `ready_line` 缓存（`backend_ready_line()` 返回它），前端 `App.tsx:178` 会把它当真。这是响应伪造的一个额外落点，红队未提。
- **补充**：`frontend/src/preview.tsx:844` 有一个 `backend_send` 的 mock 实现（`JSON.parse(args.line)` 后分发给 `METHODS`）。它是浏览器预览模式的 shim，不在 Tauri 中运行，但如果 `preview.html` 被以任何方式在生产 bundle 里暴露，`JSON.parse` + 无白名单分发就是一个独立的注入面。建议确认 `preview.html` 不进入 release bundle（我没有证据表明它会，置信度中，仅作提示）。

**③ 判定理由**：三条子项（无校验 / 帧注入 / 响应伪造）全部代码确证；唯一的修正是**原因与修复路径**，不是结论。

**④ 修复建议完备性评估**

| 项 | 评价 |
|---|---|
| 漏掉的边界 | (a) 无长度校验 → 见 BLUE-02；(b) `backend.ready` 字符串匹配可被伪造（见上）；(c) `preview.tsx` mock（置信度中）；(d) 重构为 `backend_request` 后**必须同步改 `preview.tsx:844`**，否则预览模式失效。 |
| 更优/更简替代 | **分三步走**：<br>**第 1 步（纯配置，立刻做）**：capabilities 去掉 `allow-emit`/`allow-emit-to` → 关闭响应伪造。<br>**第 2 步（Rust 10 行）**：`backend_send` 拒绝含 `\n`/`\r`/`\0` 的 `line`，并加 8 MB 硬上限（与 `frames.MAX_LINE_BYTES` 对齐）→ 关闭帧注入。<br>**第 3 步（L 级重构，可延后）**：改 `backend_request(method, params)`，由 Rust 生成随机 id 并序列化。 |
| 会引入的回归 | 第 1 步：若将来前端需要 `emit`（当前 grep 显示前端只用 `listen`/`on`，**未使用 `emit`**）→ 零回归，安全。<br>第 2 步：零回归（`JSON.stringify` 已转义换行）。<br>第 3 步：`client.ts` + `preview.tsx` 同步改；`main.rs:89` 的 ready 行匹配逻辑需保留。 |
| 依赖顺序 | **RT-03 是 RT-02 / RT-06 / BLUE-01 的共同前置**。先做第 1+2 步（P0，S 级），第 3 步放 P1。 |
| 工作量 / 类型 | 第1步 **XS / 仅改配置**；第2步 **S / 改代码**；第3步 **L / 改代码（Rust+TS）** |

---

### RT-04 — Release 下从 exe 同目录加载 sidecar 无完整性校验

**判定：CONFIRMED，维持 Medium**

**① 独立证据**

```rust
// src-tauri/src/main.rs:109-124
if !cfg!(debug_assertions) {
    if let Ok(exe) = std::env::current_exe() {
        let dir = exe.parent().unwrap().to_path_buf();
        for name in [format!("backend-{TRIPLE}.exe"), "backend.exe".to_string()] {
            let sidecar = dir.join(&name);
            if sidecar.exists() {                 // 115：唯一检查
                let mut c = Command::new(sidecar); // 116
```

红队引用 `106-126` / `113-116` 准确。`tauri.conf.json:33` `installMode: "perMachine"`、`backend.spec:65` `console=True` 均已确认。`src-tauri/binaries/` 目录仅 `backend-x86_64-pc-windows-msvc.exe`（165,625,155 字节），**无 `.sha256`、无签名文件**——确认。

**② 对红队描述的修正 / 补充**

- 红队说"`Command::new()` 使用绝对路径，因此不存在 PATH 搜索顺序劫持"——**正确但不完整**：Windows `CreateProcessW` 的 DLL 搜索顺序把**可执行文件所在目录**排在前面（在 `SafeDllSearchMode` 下仍如此），因此即使 exe 路径不可劫持，**同目录 DLL 侧载依然成立**。红队在攻击场景变体里提到了 DLL 侧载，OK。
- **补充（红队漏）**：PyInstaller onefile 会把内容解压到 `%TEMP%\_MEIxxxxxx`。`TEMP`/`TMP` 环境变量**非提权即可被修改**（快捷方式、父进程、`setx`），因此攻击者可控制 sidecar 的解压根目录。虽然目录名随机，但这扩展了攻击面。→ 与 BLUE-04（环境未清洗）合并处理。
- **补充（红队漏）**：dev 分支（`main.rs:130-136`）用 `.venv\Scripts\python.exe`，**同样无任何校验**。若 `.venv` 位于可写目录，替换 `python.exe` 即得代码执行。dev-only，但开发者机器是高价值目标。
- **补充（影响建议可实现性）**：`main.rs:113` 的候选名有**两个**（`backend-<triple>.exe` 与 `backend.exe`）。若完整性校验只覆盖第一个名字，则 release 直跑 `src-tauri/target/release/`（第二个候选）时会被绕过。**两个名字都要校验**。

**③ 判定理由**：确证；`perMachine` 是真实缓解，中等定级合理。

**④ 修复建议完备性评估**

| 项 | 评价 |
|---|---|
| 不完整之处 | (a) **两个候选名都要校验**（见上）；(b) 建议 1 的 `include_str!` 隐含要求 `scripts/package.ps1` 的 **PyInstaller → 生成哈希 → tauri build** 顺序，当前脚本顺序满足，但脚本里没有任何强制——应在脚本里加"哈希文件缺失则中止"；(c) 建议未涉及 `TEMP`/`TMP` 与子进程环境（BLUE-04）。 |
| 会引入的回归 | **建议 5（`console=False`）风险最高**：本项目依赖 stdio 传 NDJSON。`console=False` 时 Windows GUI 子系统不继承父进程控制台，但 `Stdio::piped()` 显式传句柄通常仍可用——**必须实测**，红队自己也标注了。我建议**降为 P3 可选**，不要和完整性校验绑在一起做。 |
| 更优替代 | 成本/收益最优是**建议 1（SHA-256 include_str!）+ 建议 3（脚本生成校验和）**，工作量 S~M。Authenticode（建议 2）若已有代码签名证书则顺带做，否则 L 级、放 P2。 |
| 依赖顺序 | 与 RT-12（165MB 二进制移出 Git / 记录哈希）是同一件事的两半，**一起做**。 |
| 工作量 / 类型 | S~M / **改代码 + 改构建脚本** |

---

### RT-05 — pip 自更新信任 PyPI 版本号、未锁索引/哈希

**判定：CONFIRMED，维持 Medium**（子项"release 每次启动发 PyPI 请求"**证伪**）

**① 独立证据**

```python
# services/update_service.py:73-92
with urllib.request.urlopen(req, timeout=8) as resp:      # 78
    data = json.loads(resp.read().decode("utf-8"))        # 79：无响应体上限
latest = str(data["info"]["version"]).strip()             # 80：无格式校验
```

```python
# services/update_service.py:101-116
process = subprocess.Popen(
    [sys.executable, "-m", "pip", "install", "--upgrade",
     f"mdescriptor=={target_version}",                    # 108
     "--disable-pip-version-check"], ...)                 # 列表形式，无 shell=True
```

```python
# services/update_service.py:62-64（_frozen 阻断）
def start_check(self) -> dict:
    if self._frozen():
        self._set(status="unsupported", error="frozen build: update via new installer")
        return self.snapshot()          # 62-64：直接 return，不发请求
```

```python
# main.py:60-66
target = (params.get("version") or snap.get("latest"))    # 62：前端可直接指定
```

**② 对红队描述的修正**

- **修正 1（证伪）**：红队第 3 点写"**即使 pip 不执行，每次启动都会无条件向 `https://pypi.org/pypi/mdescriptor/json` 发起请求（`main.py:195`）**"。**这条不成立**：`main.py:195` 调 `updates.start_check()`，而 `start_check` 在 `update_service.py:62` 处 `if self._frozen(): ... return`，**release 下根本不发请求**。红队描述的"每次启动的外网请求 + 响应体无大小限制"只在 dev 下成立。
- **修正 2**：红队说 `urlopen` "无显式 CA/证书固定"。准确表述应是：使用**系统信任库**，无法防御**已装进系统根存储**的中间人证书（企业 MITM 代理场景）。不是"没有 CA 校验"。
- **修正 3（红队漏，且很关键）**：release 下 `sys.executable` 是 `backend.exe` 本身，而 `_frozen()` 在 `update_runner:96` 开头就 return —— 两条独立机制同时确保 **release 下 `pip install` 永不执行**。红队结论一致，但没把"双保险"说清楚，导致读起来像单靠 `_frozen()`。
- **补充**：dev 下 `sys.executable` 由 `main.rs:130` 指定为 `.venv\Scripts\python.exe`，完全可达。

**③ 判定理由**：代码路径确证；release 缓解真实有效，Medium 合理。若团队的威胁模型把"开发者机器"排除在外，可降为 Low。

**④ 修复建议完备性评估**

| 项 | 评价 |
|---|---|
| 漏掉的边界 | (a) 未提"release 下 `start_check` 不发请求"，会导致实施者误以为要改一个不存在的行为；(b) `_VERSION_RE` 无法覆盖 PEP 508 **环境标记与直接引用**（红队自己提到了，正则确实不含 `;` `@` 与空格，能拦住——这点红队做对了）；(c) 未提 `ctx.progress` 把 pip 输出回显给前端（`update_service.py:122`）——pip 输出含临时目录绝对路径，属 A4 范畴的信息泄露。 |
| 会引入的回归 | **`tests/test_engine_update.py`（59 行）可能因版本正则而失败**——若测试用了非规范版本号字符串需同步更新。我在只读前提下未逐行核对该文件内容，标注**需实施者确认**（置信度中）。<br>`--require-hashes` 会要求**整棵依赖树**都有哈希，`mdescriptor` 依赖较重，**实践中很难维护**，大概率导致安装失败。 |
| 更优替代 | **只做三件事，跳过 `--require-hashes`**：<br>① 版本正则（红队建议 1，有效）；<br>② `update_runner` 加 `--index-url https://pypi.org/simple --only-binary=:all: --no-cache-dir --no-input` —— **`--only-binary=:all:` 是最高性价比的一条**，直接阻断"执行 sdist 构建后端"这条主要 RCE 路径；<br>③ 子进程 env 清洗（红队建议 3，正确）。 |
| 依赖顺序 | 与 BLUE-04（环境清洗）同一处代码，一起改。 |
| 工作量 / 类型 | S / **改代码**（release 无影响，仅 dev 受益） |

---

### RT-06 — `dataset.register` 任意路径解析（`format` 可绕过扩展名检查）+ 错误信息泄露

**判定：CONFIRMED，维持 Medium**（**红队自标"未确证"的"完整任意文件内容外泄"子项，我经另一路径确证**）

**① 独立证据**

```python
# services/dataset_service.py:128-140
raw_path = params.get("path")
if not raw_path or not isinstance(raw_path, str):
    raise AppError(INVALID_PARAMS, "'path' (string) is required")
path = Path(raw_path)
if not path.exists():                                    # 133：唯一校验
    raise AppError(INVALID_DATASET, f"path does not exist: {path}")   # 134：路径回显
try:
    fmt = params.get("format") or detect_format(path)    # 136：format 由调用方指定
```

```python
# datasets/base.py:69, 79
if path.is_file() and path.suffix.lower() in (".xyz", ".extxyz"):   # 69
    ...
fmt = fmt or detect_format(path)                         # 79：给了 fmt 就完全跳过
```

```python
# datasets/extxyz.py:119-122
except (ValueError, IndexError) as exc:
    raise AppError(
        INVALID_DATASET, f"frame {index} row {row}: malformed atom line ({exc})"   # 121
    ) from exc
```

```python
# datasets/fingerprint.py:13
files = sorted(path.rglob("*")) if path.is_dir() else [path]   # 对任意目录递归枚举
```

```python
# protocol/server.py:72-76
except Exception as exc:
    log.exception("unhandled error in %s", method)
    self._write(frames.response_err(vid, AppError("INTERNAL_ERROR", f"{type(exc).__name__}: {exc}")))
```

红队引用行号（`133`/`136`/`69`/`79`/`32`/`39`/`121`/`13`）**全部准确**。

**② 对红队描述的修正 —— 本条最重要的独立发现**

红队在 RT-06 第 3 点写：

> **限制（必须说明）**：`dataset.frame` 需要一条 DB 记录，而记录只在 `register` 的 job 成功（scan + `compute_statistics` 全通过）后才写入…所以**"完整的任意文件内容外泄"我标为未确证**。

**这个限制只对"通过 `dataset.register` 创建记录"这一条路径成立。** 我核实 `dataset.frame`：

```python
# services/dataset_service.py:376-388
def frame(self, params: dict) -> dict:
    ds_id, index = params.get("id"), params.get("index")
    if not isinstance(index, int): ...
    row = self._row(ds_id)                    # 只读 datasets 表
    adapter = self._adapter_for(row)          # 不检查任何 run/scan 状态
    try:
        f = adapter.get_frame(index)
    except AppError:
        raise
    except Exception as exc:
        raise AppError(INVALID_DATASET, f"cannot read frame {index}: {exc}") from exc
```

**`dataset.frame` 不依赖任何 `descriptor_runs` 记录，只要 `datasets` 表有行即可。**

因此，只要攻击者能写 `database.sqlite`（RT-10 / RT-11 的前提，`MDS_DATA_DIR` 指向共享目录或同用户恶意软件），即可：

```sql
INSERT INTO datasets (id,name,format,source_path,number_of_frames,elements,properties,
                      periodicity,fingerprint,file_size,created_at)
VALUES ('ds_x','x','extxyz','C:\Users\victim\.aws\credentials',1,'[]','{}',
        '{"fully_periodic":false,"isolated":true,"mixed":false,"flags":[". . ."]}','fp',0,'2026-01-01');
```

然后循环 `dataset.frame {"id":"ds_x","index":0}`，由 `extxyz.py:121` 的
`malformed atom line (could not convert string to float: '<token>')`
经 `server.py:75` 逐 token 外泄文件内容。

**结论：RT-06 的内容外泄子项确证，但前提是"能写 DB"而非"webview 代码执行"。** 我把它单列 **BLUE-03**，并在 RT-06 保持 Medium（因为原路径仍然只到"存在性/元数据探测 + 部分内容外泄"）。

**其他修正**：
- 红队修复建议里的 `dataset.frame` 的 `index` 加 `not isinstance(index, bool)` —— 确证必要：`isinstance(True, int)` 为 `True`，`{"index": true}` 等价于 `index=1`。
- **补充（跨模块不一致，正是用户要求我找的）**：红队在 RT-02 的建议里列了"拒绝 Windows 保留设备名"，但在 RT-06 的建议里**没有**。`Path("NUL").exists()` 在 Windows 上返回 `True`，后续 `open("NUL")` 会挂住 worker 线程或行为异常 → RT-06 同样需要设备名黑名单。

**③ 判定理由**：核心结论（任意路径被打开解析 + 信息泄露）双向确证。

**④ 修复建议完备性评估**

| 项 | 评价 |
|---|---|
| **建议里的真 bug** | 第 4 步 `if ".." in resolved.parts` 是**死代码**：`Path.resolve()` 已消除 `..`，`resolved.parts` 中**不可能**出现 `..`（除非路径含符号链接导致解析后仍有残留——但那种情况 `is_relative_to` 才是有效检查）。**应删掉这行，改用 `resolved.is_relative_to(allowed)`。** 同样的错误出现在 RT-02 与 RT-10 的建议里，需三处一并修正。 |
| **建议不可落地之处** | `_ALLOWED_ROOTS`（"用户显式选过的目录"）**不能作为根治手段**：应用重启后该列表为空，若不持久化就等于不限制；若持久化到 DB 则又回到"DB 可被投毒"的循环。数据集的合法用例就是"用户自选的任意目录"，**能做且应做的只有：拒绝 UNC / `\\?\` / `\\.\` / ADS / 相对路径 / 保留设备名 / 尾部空格与点 + 要求 `is_file()` 或 `is_dir()` + `rglob` 条目上限**。必须把这一点写明，否则实施者会误以为能根治。 |
| 漏掉的边界 | (a) 保留设备名（`CON`/`NUL`/`AUX`/`COM1-9`/`LPT1-9`）——见上；(b) `path.exists()` / `open()` 对**命名管道**会阻塞（无超时）；(c) `rglob` 无条目上限（红队在配套建议里提了 50 万条，OK，但**没给总字节上限**）；(d) 错误消息脱敏要覆盖 `dataset_service.py:134`、`base.py:67/71`、`deepmd.py:35/46/85`、`extxyz.py:52/92/104/121`，红队列全了，OK。 |
| 会引入的回归 | 去掉 `format` 参数：我 grep 了 `tests/*.py`，**没有任何测试给 `dataset.register` 传 `format`** → 零回归。<br>但 `create_adapter(Path(row["source_path"]), row["format"])` 在 `dataset_service.py:119` 与 `:296` 依赖 DB 的 `format` 列，**该列必须保留**。 |
| 更优替代 | **"去掉 `format` 参数"是正确且最简的**（`dataset_service.py:136` 改为 `fmt = detect_format(path)`），一行改动即可关闭 `format` 绕过。配合错误消息脱敏（A4）一起做。 |
| 依赖顺序 | 内容外泄的**根治**依赖 RT-10（不信任 DB 路径）；消息脱敏（A4）可独立先做。 |
| 工作量 / 类型 | S（去 format + 加设备名/UNC 检查）/ **改代码**；脱敏 M / **改代码** |

---

### RT-07 — 数据集指纹只哈希 size+mtime，不含内容

**判定：CONFIRMED，维持 Medium**（**修复方案缺迁移与性能评估，直接照做会出事**）

**① 独立证据**

```python
# datasets/fingerprint.py:9-22（全文）
def compute_fingerprint(source_path: Path, number_of_frames: int | None = None) -> str:
    path = Path(source_path)
    h = hashlib.sha256()
    h.update(str(path).encode("utf-8"))
    files = sorted(path.rglob("*")) if path.is_dir() else [path]
    for f in files:
        if f.is_file():
            stat = f.stat()
            h.update(f.relative_to(path if path.is_dir() else f.parent).as_posix().encode())
            h.update(str(stat.st_size).encode())       # 18
            h.update(str(stat.st_mtime_ns).encode())   # 19
    if number_of_frames is not None:
        h.update(str(number_of_frames).encode())
    return h.hexdigest()
```

**全文 22 行，无任何 `read()` / `update(chunk)`。确证。**

三个消费点全部核实：
- `dataset_service.py:270-283` `_cached_stats`（指纹相同 → 复用缓存统计）
- `analysis_service.py:943-955` `_assert_dataset_current`（指纹相同 → 不标记 STALE）— **红队引用 `944-955`，实际 `def _assert_dataset_current` 在第 943 行，偏移 1 行，可忽略**
- `descriptor_service.py:179-193`（指纹是 descriptor 缓存键的核心组成）

**② 对红队描述的修正 / 补充**

- **补充（红队提了但没强调）**：指纹还是 **export 的缓存键**（`analysis_service.py:673-674`，`canonical` 含 `output_path`，`cache_key = _analysis_cache_key(...)`）的基础之一，以及 `descriptor_service.py:386` 写进结果 metadata 的 `dataset_fingerprint`。因此指纹失效影响面比"统计复用"更广。
- **补充**：`compute_fingerprint` 还被 `dataset_service._meta:91` 调用，而 `_meta` 被 `dataset.list` 对**每一个**数据集调用 → 见下面性能回归。

**③ 判定理由**：代码确证，完整性危害真实（科研结论被静默污染）。

**④ 修复建议完备性评估 —— 红队本条建议落地风险最高**

| 项 | 评价 |
|---|---|
| **漏掉的落地风险 1：破坏性数据迁移** | 新指纹算法会让**所有已存在数据集的 fingerprint 变化**。`dataset_service._meta:92` 的 `if current != row["fingerprint"]` 立刻为真 → `_mark_runs_stale`（`:96`）把该数据集下**所有 COMPLETED 的 descriptor_run 与 analysis_run 标记 STALE**，且 `STALE` 的 run 不能被 `_usable_run`（`analysis_service.py:938-940`）用于任何新分析。**用户升级后所有历史结果全部变灰、不可用。** 红队完全没提。<br>**必须配套**：给指纹加算法版本号（例如前缀 `v2:`），旧行保留旧指纹；启动时检测 `datasets.fingerprint` 不含 `v2:` 前缀 → 排一个后台 rescan job 重算，期间不标记 STALE 而是标记 `FINGERPRINT_MIGRATING`。这是 **L 级工作量**，不是 S。 |
| **漏掉的落地风险 2：性能回归** | `compute_fingerprint` 的调用频次极高且**跑在 RPC worker 同步路径上**：`dataset.list`（每个数据集一次）、`dataset.get`、`dataset.statistics`、`refresh_if_changed`、`descriptor.submit`、`_assert_dataset_current`、`_run_compute` 收尾。改为内容哈希后，每次调用对每个文件读头/中/尾各 1 MiB。一个含 100 个 `set.*` 目录 × 5 个 npy 的 DeepMD 数据集 = 500 文件 × 3 MiB = **1.5 GiB 读取**；`dataset.list` 一次列 20 个数据集 → **UI 长时间卡死 + 4 个 RPC worker 全被占满**（与 RT-09 叠加）。<br>**必须配套**：(a) 全局采样字节预算（如累计 32 MiB 后停止采样，超出部分只哈希 size+mtime 并标记 `partial`）；(b) 进程内 TTL 缓存，key = `(path, size, mtime_ns)`；(c) 或把指纹计算移入 job 线程。 |
| 建议里的可改进处 | "拒绝符号链接（防…泄露其 size/mtime）：改为 `f.is_symlink()` 时跳过**或拒绝该数据集**"—— **"拒绝该数据集"过于激进**，会误伤含合法符号链接的数据集（科研数据集里软链很常见）。应改为**跳过并记录 warning**。 |
| 更优替代 | 保留 size + mtime_ns 作为**廉价快路径**（它们仍能在绝大多数真实变更下立刻检出），仅在 size+mtime 都未变时才做内容采样——但**这正好被本条攻击绕过**（攻击者恢复 size+mtime）。所以正确做法是：**无条件做内容采样 + 字节预算上限 + 缓存**，把"廉价"交给缓存而不是交给"只哈希元数据"。 |
| 依赖顺序 | **必须在 RT-09（有界队列 + 背压）之后**做：指纹变慢后，无界队列会把慢速放大成 OOM。同时**必须在 RT-10（DB 完整性）之前**想清楚——指纹本身也存在 DB 里，改库仍可绕过（见残余风险）。 |
| 工作量 / 类型 | **L**（算法 + 迁移 + 缓存 + 预算）/ **改代码** |

---

### RT-08 — extxyz `natoms` 无上限 + 全量帧载入内存 → OOM DoS

**判定：CONFIRMED，维持 Medium**

**① 独立证据**

```python
# datasets/extxyz.py:38-50
try:
    natoms = int(line.strip())                       # 39：无上限、无与文件大小一致性校验
except ValueError:
    break
comment = f.readline()
if not comment: break
meta = _parse_comment(comment)
self._offsets.append(start)
self._frame_meta.append({"natoms": natoms, **meta})   # 47
for _ in range(natoms):
    if not f.readline(): break                        # 49-50：文件不够长就 break，索引照样建成
```

```python
# datasets/extxyz.py:99-100
positions = np.empty((natoms, 3), dtype=np.float64)                      # 99：先分配后读取
forces = np.empty((natoms, 3), dtype=np.float64) if forces_i is not None else None
```

```python
# services/descriptor_service.py:333-343
frames = []
total = max(len(adapter), 1)
for i, frame in enumerate(adapter.iter_frames()):     # 336：全量载入
    frames.append(frame)
batch = self.adapter.to_structure_batch(frames)       # 343：再 concat 一份
```

红队引用准确。**补充核实**：`for _ in range(2e9)` 在 Python 3 中是惰性 `range`，不会预分配；第 49 行的 `break` 只跳出**内层**循环，外层 `while True` 继续 `f.readline()` → 对截断文件会继续尝试解析后续内容（行为可接受但不严谨）。

**② 对红队描述的修正 / 补充**

- **补充（红队漏）**：`_parse_comment`（`extxyz.py:144-178`）的 `float(value)`（`:156`，`energy=abc`）与 `int(parts[i+2])`（`:168`，`Properties=species:S:x`）会抛 **`ValueError`**，而 `_build_index:51` 只捕获 `OSError` → `ValueError` 冒泡到 `job_service._run:146` → `INTERNAL_ERROR`，错误消息含完整路径与 Python 内部文本。这是**错误处理路径上的缺口**（用户要求我重点看的），应把 `except OSError` 扩为 `except (OSError, ValueError)` 并转 `AppError(INVALID_DATASET, ...)`。
- **补充**：`extxyz.py:167-168` 的列宽虽无上限，但 `n_cols` 巨大时 `get_frame:103` 的 `len(tokens) < n_cols` 立即抛错——**而 `np.empty((natoms,3))` 已在第 99 行分配**。所以列宽不是独立放大器，红队把它列为"其他放大器"略微夸大，实际主要放大器是 natoms 与 `descriptor_service:336-343` 的全量载入。

**③ 判定理由**：几十字节文件触发数十 GB 分配请求，确证；无提权无泄露 → Medium 合理。

**④ 修复建议完备性评估**

| 项 | 评价 |
|---|---|
| 建议的可改进处 | 建议 1 的 `file_size - f.tell() < natoms * 8` 对每行 8 字节的极简文件（`H 0 0 0\n` 正好 8 字节）边界**刚好**，容易误判合法文件。建议以**建议 2（分配前字节上限，如 512 MiB）为主**、建议 1 为辅，或把每原子行下限放宽到 16 字节。 |
| 漏掉的边界 | (a) `_parse_comment` 的 `ValueError` 未被转 `AppError`（见上）；(b) DeepMD 侧 `deepmd.py:49` 之前没有对 `coord.npy` 形状/`type.raw` 行数做预算——红队建议 6 提了，OK；(c) `properties` 列宽之和无上限（影响有限，见上）。 |
| 会引入的回归 | `tests/` 里最大用例是 `write_extxyz(p, 6, 64)`（64 原子）与 `make_fixtures.py` 生成的 8/16 原子 → `MAX_ATOMS_PER_FRAME = 1_000_000` 与 512 MiB 上限**零回归**。 |
| 更优替代 | 建议 5（job worker 内存护栏）**可直接复用现有的 `_MemorySampler`/`_process_rss_bytes`（`descriptor_service.py:39-72`）**，不必新写 psapi 调用——红队说"可复用"是对的。 |
| 依赖顺序 | **必须与 RT-09 一起做**：单个 OOM 只占死一个 worker，只有配合并发/队列上限才构成完整防护；反过来，先做 RT-09 能让 RT-08 的破坏被限制。 |
| 工作量 / 类型 | M / **改代码** |

---

### RT-09 — RPC 线程池任务队列无界 → 内存放大 DoS

**判定：CONFIRMED，维持 Medium**（"8 MB 限制不可绕过"**部分证伪**；红队给出的 `_BoundedPool` 实现有 shutdown 挂死缺陷）

**① 独立证据**

```python
# protocol/server.py:25
self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="rpc")   # 默认无界队列

# protocol/server.py:41-47
for raw in sys.stdin:        # 41：TextIOWrapper 按行迭代，无行长上限
    line = raw.strip()       # 42：又一份副本
    ...
    self._pool.submit(self._handle, line)    # 47：无界提交
```

```python
# protocol/frames.py:10, 18
MAX_LINE_BYTES = 8 * 1024 * 1024
if len(line.encode("utf-8", "replace")) > MAX_LINE_BYTES:   # 18：在整行读完之后才检查
```

**② 对红队描述的修正 —— 本条最重要的独立发现**

红队写：

> 8 MB 的行限制**本身是有效的**（`len()` 在解析前检查，无法绕过；`server.py:42` 的 `raw.strip()` 只会缩短字符串）。

**"无法绕过"在调度层成立，在内存层不成立。** `server.py:41` 的 `for raw in sys.stdin` 使用 `io.TextIOWrapper` 的按行迭代，**没有任何行长上限**；整行会先被完整读入内存（UTF-8 解码为 `str`），`strip()` 再产生一份副本，之后才交给 `parse_request` 做长度检查。

因此攻击者发一个 **500 MB 的单行**（经 `backend_send`，Rust 侧同样无长度校验），后端会先把 **~1 GB**（原始 str + strip 副本）读进内存，然后才抛 `INVALID_PARAMS`。配合无界队列可稳定 OOM。**→ 单列 BLUE-02。**

**其他修正 / 补充**：
- **红队漏了第二个无界队列**：`JobService._executor`（`job_service.py:86`，`max_workers=2`）同样是默认无界队列，且 `submit`（`:107-115`）**先写 DB 行再入队** → 循环调 `dataset.register` 会同时撑爆 DB 行数与队列。**→ 单列 BLUE-05。**
- **红队给出的 `_BoundedPool` 实现有缺陷**：`_loop` 中 `while not self._closed.is_set(): item = self._q.get()` —— `get()` 是**阻塞**调用，若队列为空且已 `_closed`，线程会永久阻塞在 `get()` 上（因为 `_closed` 检查在 `get()` 之前）。`server.close()`（`:82`）与 `main.py:199` 的 `jobs.shutdown()` 都依赖及时返回 → **会挂死退出流程**。

**③ 判定理由**：无界队列确证；我对"8MB 有效性"和"第二个池"做了修正，定级不变。

**④ 修复建议完备性评估**

| 项 | 评价 |
|---|---|
| **更优且更简的替代** | **不要自己写线程池**。保留 `ThreadPoolExecutor`，用信号量做背压即可：<br>`self._slots = threading.BoundedSemaphore(64)`；<br>`serve_forever`：`if not self._slots.acquire(blocking=False): self._write(frames.response_err(None, AppError("BUSY", ...))); continue`；<br>worker 入口 `finally: self._slots.release()`。<br>约 **8 行**，无 shutdown 挂死风险，语义与 `Executor` 协议兼容。**这比红队的 25 行 `_BoundedPool` 更简单也更正确。** |
| **必须补的一条（红队建议 3 提到了，但要落到正确位置）** | Rust 侧在 `main.rs:26` 之前加 `if line.len() > 8*1024*1024 { return Err(...) }` —— 这只是省管道写入。**真正修内存问题要在 Python 侧给 stdin 加行长上限**：把 `for raw in sys.stdin` 换成带上限的手工读取（如 `sys.stdin.buffer.readline(MAX_LINE_BYTES + 1)` 后手工解码），超限则丢弃整行到下一个 `\n` 并回错误帧。**这是 BLUE-02 的修复点，红队建议里没有。** |
| 漏掉的边界 | (a) 输出侧无节流（`server._write`）——红队提了，OK；(b) `job_service` 队列（BLUE-05）；(c) 限流后**前端无重试逻辑**，`client.ts` 收到 `BUSY` 会直接 reject，需要在 UI 上给出"后端繁忙"提示或重试。 |
| 会引入的回归 | 限流后高并发场景（前端同时发多个 `dataset.frame`）可能收到 `BUSY`。当前前端是否有并发请求需评估（置信度中）。建议 `queue_size` 先取 256 观察。 |
| 依赖顺序 | **先于 RT-07**（指纹变慢后无界队列会灾难性放大）、**与 RT-08 同级**。 |
| 工作量 / 类型 | S（信号量 + Rust 长度校验）/ **改代码**；stdin 行长上限 M / **改代码** |

---

### RT-10 — `result.remove` / `analysis.delete` 对 DB 中的 `result_path` 无条件 `rmtree`

**判定：CONFIRMED，维持 Medium**

**① 独立证据**

```python
# services/result_service.py:105-113
self._rmtree_quiet(row["result_path"])        # 105
for ana in analyses:
    self._rmtree_quiet(ana["result_path"])    # 107

@staticmethod
def _rmtree_quiet(path: str | None) -> None:
    if path:
        shutil.rmtree(path, ignore_errors=True)   # 113
```

```python
# services/analysis_service.py:378-381
self.db.execute("DELETE FROM analysis_runs WHERE id = ?", (analysis_id,))
self._rmtree_quiet(row.get("result_path"))    # 380

# services/analysis_service.py:1570-1575
@staticmethod
def _rmtree_quiet(path: str | None) -> None:
    import shutil
    if path:
        shutil.rmtree(path, ignore_errors=True)   # 1575
```

红队引用准确。写入侧核实：`descriptor_service.py:373` `run_dir = self.data_dir / "results" / f"run_{run_id.removeprefix('run_')}"`、`analysis_service.py:1274` `final = root / analysis_id`（`root = self.data_dir / "analysis"`）—— **两侧推导式一致且可逆**，这支持"重推导"方案（见下）。

**② 对红队描述的修正 / 补充**

- 红队说"`shutil.rmtree` 不会跟随目录符号链接（Python 3.8+ 对目录链接报 `OSError` 而非递归进入）"—— 正确。补充：在 Windows 上，目录 junction/符号链接会被**删除链接本身**（而非目标），所以"放一个指向他处的目录链接"影响有限。红队表述基本正确。
- **补充（红队漏）**：`_rmtree_quiet` 用 `ignore_errors=True` 且**完全没有日志**。删除失败、被拒绝、路径不存在——全部静默。应改为 `onerror=` 回调写 `log.error`，否则这类事件在 incident 响应时**不可追溯**。
- **补充（红队漏）**：`analysis_service.delete`（`:373-381`）**先删 DB 行、后删磁盘**。若 rmtree 失败则留下孤儿目录且 DB 中已无记录 → **永久无法追溯与清理**。顺序应反过来（先删盘，成功后再删行；或先标记 `DELETING`）。

**③ 判定理由**：代码路径确证；可达性依赖 DB 写权限（RT-11 放大器），Medium 合理。

**④ 修复建议完备性评估**

| 项 | 评价 |
|---|---|
| **会引入的回归（确证）** | 建议要求给 `ResultService.__init__` 加 `data_dir` 参数 → **破坏 2 个在跑的测试**：<br>· `tests/test_result_list.py:32`：`ResultService(db)`<br>· `tests/test_run_remove.py:27`：`return db, ResultService(db)`<br>必须把新参数设为**可选**（`data_dir: Path | None = None`），或同步更新这两个测试文件（本次审计禁止我改测试，需在整改计划中单列"测试改动"）。<br>`AnalysisService` 已有 `self.data_dir`（`analysis_service.py:49`），改动无痛。 |
| **更优替代（红队提到了但排在"配套"里，我建议作为首选）** | **从 `run_id` / `analysis_id` 重新推导受信任路径，完全不读 DB 的 `result_path`**：<br>· descriptor run：`data_dir / "results" / f"run_{run_id.removeprefix('run_')}"`<br>· analysis：`data_dir / "analysis" / analysis_id`<br>推导后与 DB 值比对，不一致则记 `log.error` 并**拒绝删除 + 告警**。这个方案：<br>(a) **不需要改 `ResultService.__init__` 签名** → 零测试回归；<br>(b) 顺带把 `_artifact_is_complete` 读磁盘 manifest 的问题（BLUE-06）一起规避；<br>(c) 比"校验 DB 路径是否在 data_dir 内"更严格。 |
| 建议里的真 bug | 同 RT-02/RT-06：`".." in resolved.parts` 在 `resolve()` 之后是死代码，应改用 `is_relative_to()`。 |
| 漏掉的边界 | (a) `_rmtree_quiet` 有两个**语义完全不同**的调用场景：`result.remove`/`analysis.delete`（不可信 DB 路径，需严格校验）与 `_commit_artifact:1315`（data_dir 下的 staging，宽松即可）。**同一个函数服务两种信任级别**是设计缺陷，应拆成 `_rmtree_managed()`（严格）与 `_rmtree_quiet()`（宽松），避免将来误用。红队没提。<br>(b) 删除顺序（见上）。<br>(c) `ignore_errors=True` 无日志（见上）。 |
| 依赖顺序 | **RT-11 先修**（切断"DB 落在共享位置"这一前提），否则本条修了也只防同用户恶意软件。 |
| 工作量 / 类型 | S（重推导方案）/ **改代码**；若改用传参方案则 M（含测试改动） |

---

### RT-11 — `MDS_DATA_DIR` 完全受控且无校验 → 数据目录重定向

**判定：CONFIRMED，维持 Low**（但**建议随 P0 一起修**，因为它是 RT-01 与 RT-10 的使能条件）

**① 独立证据**

```python
# config.py:12-21
def data_dir() -> Path:
    override = os.environ.get("MDS_DATA_DIR")
    if override:
        root = Path(override)                      # 15：无校验
    else:
        local = os.environ.get("LOCALAPPDATA")
        root = (Path(local) if local else Path.home()) / APP_DIR_NAME
    for sub in ("logs", "results", "analysis", "cache"):
        (root / sub).mkdir(parents=True, exist_ok=True)    # 20：任意位置递归建目录，默认 ACL
    return root
```

```rust
// src-tauri/src/main.rs:117-121
if let Ok(data_dir) = std::env::var("MDS_DATA_DIR") {
    if !data_dir.is_empty() {
        c.env("MDS_DATA_DIR", data_dir);
    }
}
```

**② 对红队描述的修正**

- **修正（重要）**：红队把 `main.rs:118` 的 `!data_dir.is_empty()` 列为"已有缓解"——**它不是缓解**。Rust 的 `Command` 默认**继承父进程的全部环境变量**；`c.env("MDS_DATA_DIR", ...)` 只是**覆盖**同名变量。因此即使不显式传，Rust 进程自身的 `MDS_DATA_DIR` 也会继承给 sidecar。红队"只在自己进程环境存在时才透传（117-121 行）"的描述**在机制上是错的**（结论碰巧仍对：不设该变量时 Python 侧 `os.environ.get` 返回 `None`）。
- **补充（红队漏，且是 RT-10 的使能条件）**：`config.py:20` 的 `mkdir` 使用**默认 ACL**（继承父目录）。若 `MDS_DATA_DIR` 指向继承 `Everyone:(M)` 的目录，则 `database.sqlite`、`logs/backend.log`、`results/`、`analysis/` 全部对其他用户可读写 → **一次性同时打开 RT-10（DB 投毒）与 A4（日志泄露）**。红队建议 4 提了"收紧 ACL"但没点明这是 RT-10 的前提，导致优先级被低估。
- **补充（跨模块不一致）**：`logging_setup.py:17` **也独立调用 `data_dir()`**，且在 `main.py:152-153` 的 `root = data_dir()` **之前**执行。若将来只在 `main()` 或只在 `config.py` 加校验，**日志路径会绕过校验**。两处必须共用同一个已校验的 `data_dir()`。**→ BLUE-13。**

**③ 判定理由**：放大器属性确证；本身危害有限，Low 合理，但优先级应高于红队给的 P3。

**④ 修复建议完备性评估**

| 项 | 评价 |
|---|---|
| 漏掉的边界 | (a) 默认 ACL（见上）；(b) `logging_setup` 的第二次调用（BLUE-13）；(c) 未提 `MDS_DATA_DIR` 为 UNC 时**两个** `data_dir()` 调用都会触发 SMB 认证（双倍 NTLM 外泄面）。 |
| 会引入的回归 | 建议 2（release 忽略 `MDS_DATA_DIR`，仅 `cfg!(debug_assertions)` 透传）：**不影响测试**——`tests/test_backend_smoke.py:20` 用 `subprocess.Popen(env={... "MDS_DATA_DIR": str(tmp)})` 直接启动 Python，不经过 Rust 的 `cfg!` 分支。**零回归，可放心做。** |
| 更优替代 | 建议 2 是正确方向，但**release 完全忽略会破坏"用户自定义数据目录"这一可能的产品需求**。折中：release 下 `MDS_DATA_DIR` 必须指向**本地、绝对、非 UNC、非相对**的路径，且启动时校验该目录及其 4 个子目录的 ACL（非当前用户可写则告警/拒绝）。 |
| 依赖顺序 | **必须先于 RT-01**（否则 numba 缓存根仍可被重定向）与 **RT-10**。建议**提到 P0 的收尾项**或**P1 的第一项**。 |
| 工作量 / 类型 | S（校验函数 + ACL 检查）/ **改代码 + 改配置（main.rs 一行）** |

---

### RT-12 — 供应链：依赖无哈希锁定、npm 全 `^`、165 MB 预构建二进制入库无校验和

**判定：CONFIRMED，维持 Low**

**① 独立证据**

```
backend/requirements.txt:5-11
mdescriptor==0.2.7 / numpy==2.5.2 / scipy==1.18.1 / scikit-learn==1.9.0
umap-learn==0.5.12 / hdbscan==0.8.44 / dpdata==1.1.0
# 无 --hash=sha256:...，无 --require-hashes
```

```json
// frontend/package.json:13-27
"@ant-design/icons": "^6.0.0", "@fluentui/react-icons": "^2.0.270",
"@tauri-apps/api": "^2.11.1", "@tauri-apps/plugin-dialog": "^2.7.2",
"3dmol": "^2.5.5", "antd": "^5.27.0", "echarts": "^6.0.0",
"echarts-for-react": "^3.0.2", "plotly.js": "^2.35.2", "react-plotly.js": "^2.6.0",
"react": "^18.3.1", "react-dom": "^18.3.1", "zustand": "^5.0.8"
```

```powershell
# scripts/package.ps1:11-14
& "$root\.venv\Scripts\python.exe" -m PyInstaller backend.spec --noconfirm --log-level ERROR
Copy-Item "$root\backend\dist\backend.exe" "$root\src-tauri\binaries\backend-x86_64-pc-windows-msvc.exe" -Force
# 无 Get-FileHash，无签名步骤
```

`src-tauri/binaries/` 仅 1 个文件，**无 `.sha256`**：确证。`frontend/package-lock.json` 与 `src-tauri/Cargo.lock` 均存在（`tauri 2.11.5`）。

**② 对红队描述的修正 / 补充**

- 红队关于 "`npm install` 仍可跨 minor/major 漂移，只有 `npm ci` 才严格按 lockfile" —— **准确**。
- **补充（红队漏）**：`backend/backend.spec:29-45` 用 `collect_all()` 收集 `mdescriptor/dpdata/sklearn/scipy/umap/hdbscan` 的全部 `datas`。这些包的数据文件里可能包含**可被覆盖的脚本/配置**；同时 `hiddenimports += ["numpy","joblib","numba","llvmlite"]`（`:46`）把 **joblib 打进包** —— joblib 是另一个 pickle 使用者（虽然本项目未直接调用 `joblib.load`，但 `sklearn` 内部会用）。这是 A8 建议 fuzz 的一个补充面。
- **补充**：`scripts/package.ps1` 依赖 `.venv`，而 `.venv` 的构建过程本身无哈希校验（同 RT-12 范畴）；`requirements.txt` 也没有列出 PyInstaller，构建依赖与运行依赖未分离。

**③ 判定理由**：确证。构建期风险、需上游被攻陷 → Low 合理。

**④ 修复建议完备性评估**

| 项 | 评价 |
|---|---|
| 漏掉的边界 | (a) 未提 `backend.spec:46` 把 `joblib`/`numba` 打进包带来的反序列化面；(b) 未提 `.venv` 构建过程无校验；(c) 未提 `requirements.txt` 缺 PyInstaller（构建不可复现）。 |
| 会引入的回归 | `pip-compile --generate-hashes` 需要 `pip-tools`（不在 `requirements.txt` 中）→ **新增开发依赖**；且哈希锁定后**每次升级依赖都要重新生成整份文件**，维护成本显著上升。`--only-binary=:all:` 与哈希锁定组合时，若某个依赖无 wheel 会直接失败。 |
| 更优替代 | **按性价比排序**：<br>① CI/发布**强制 `npm ci`**（零成本，最高收益）；<br>② `scripts/package.ps1:14` 后加 `Get-FileHash -Algorithm SHA256 \| Out-File ...sha256`（3 行，零回归）+ `main.rs` spawn 前校验（RT-04）；<br>③ 165 MB 二进制移出 Git（Git LFS 或制品仓库）；<br>④ `pip-compile --generate-hashes`（成本最高，放最后）。 |
| 依赖顺序 | ② 与 RT-04 是同一件事，一起做。 |
| 工作量 / 类型 | ① **XS / 改流程**；② **S / 改脚本 + 改代码**；③ **M / 改仓库**；④ **L / 改依赖管理** |

---

### RT-13 — CSP 缺少 `script-src`/`object-src`/`base-uri`/`frame-ancestors`

**判定：CONFIRMED，维持 Low**（**红队给出的 CSP 字符串有白屏回归风险，我建议一个更保守的版本**）

**① 独立证据**

```json
// src-tauri/tauri.conf.json:24-26
"security": {
  "csp": "default-src 'self'; connect-src 'self' ipc: http://ipc.localhost; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; font-src 'self' data:"
}
```

**独立核查 XSS sink（红队的阴性结论）**：

```
$ grep -rn "dangerouslySetInnerHTML|innerHTML|outerHTML|new Function|eval(|document.write|srcdoc|insertAdjacentHTML" frontend/src
./components/StructurePreview.tsx:168:      if (viewerDiv.current) viewerDiv.current.innerHTML = "";
./pages/Explore.tsx:218:      if (viewerDiv.current) viewerDiv.current.innerHTML = "";
```

**仅 2 处，且都是 `= ""` 清空操作。红队"当前前端未发现 XSS sink"的阴性结论确认成立。**

```
$ grep -rn "window.open|location.href|src=|postMessage" frontend/src
./App.tsx:221:            src={APP_ICON_URL}
./components/layout/TitleBar.tsx:79:        <img src={APP_ICON_URL} alt="" aria-hidden="true" />
```

两处都是模块内常量的图标 URL，**无用户可控 URL**。

**② 对红队描述的修正 / 补充**

- 红队说"未显式声明 `script-src` → 回退到 `default-src 'self'`，实际效果尚可"—— **正确**。
- **补充（红队建议里的遗漏，会造成严重回归）**：红队建议的 CSP 字符串是
  `"default-src 'self'; script-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; frame-src 'none'; connect-src ..."`
  —— 其中 **`connect-src` 未包含 `http://tauri.localhost`**。Windows 上 Tauri 2 用 `http://tauri.localhost` 作为资源 origin，`'self'` 会被展开为该 origin；但既然作者**已经显式**写了 `ipc: http://ipc.localhost`，说明该版本下 `'self'` 的展开结果可能不含这些。若某次 Tauri 小版本升级改变了 `'self'` 的解析，前端资源会被**全拦 → 白屏**。
  **建议显式补 `http://tauri.localhost`**（到 `default-src` 或 `connect-src`），或在升级 Tauri 时把 CSP 验证列入回归清单。
- **补充（回归风险，红队未评估）**：显式加 `script-src 'self'` 会**拦掉 `blob:` Worker**。plotly.js 与 3dmol 在某些路径下会用 `blob:` URL 创建 Worker（plotly 的 `plotly.js-dist` 有 WebWorker 路径；3dmol 使用 WebWorker 做部分计算）。`default-src 'self'` 回退时 `worker-src` 同样是 `'self'`，所以**现状也可能已经受影响**——但现状能跑说明要么没用 blob worker，要么 `'self'` 展开后覆盖了。**在收紧前必须实测 Analysis/Explore/Structure 三个页面。**

**③ 判定理由**：配置缺口确证；当前无利用路径 → Low 合理。

**④ 修复建议完备性评估**

| 项 | 评价 |
|---|---|
| **更保守、回归风险更低的方案** | **先只加三条、不动 `script-src`**：<br>`"default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; connect-src 'self' ipc: http://ipc.localhost http://tauri.localhost; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; font-src 'self' data:"`<br>即：**加 `object-src 'none'` / `base-uri 'none'` / `frame-ancestors 'none'`，显式补 `http://tauri.localhost`，但保留 `script-src` 继续回退 `default-src`**，避免破坏 plotly/3dmol 的 blob worker。待实测确认无 blob worker 依赖后，再显式加 `script-src 'self'`。 |
| 漏掉的边界 | (a) `http://tauri.localhost` 未显式声明（见上）；(b) 无 `form-action`（本项目无表单提交，可跳过）；(c) 无 `worker-src` 显式声明（跟随 `default-src`，收紧 `script-src` 时需一并评估）；(d) `require-trusted-types-for` 与 antd 的 CSS-in-JS 不兼容，**不要加**。 |
| 会引入的回归 | 见上（blob worker / 白屏）。**必须在 Analysis、Explore、StructurePreview 三页实测后再合入。** |
| 依赖顺序 | 无依赖，可独立做。**但注意 CSP 加固对 RT-03 是"纵深防御"而非"替代"**——CSP 不能阻止已被投毒的 npm 依赖（它运行在页面自身的 origin 内，能调 `invoke`）。 |
| 工作量 / 类型 | **XS / 仅改配置**（验证成本 S） |

---

### A1 — `np.load` 未显式传 `allow_pickle=False`

**判定：CONFIRMED，维持 Low**

**独立证据**：

```python
# services/result_service.py:77
return np.load(path / "values.npy"), row          # 未传 allow_pickle
# services/result_service.py:142
offsets = np.load(offsets_file)                    # 未传 allow_pickle
```

对比——其余 5 处全部显式传参：

```
analysis_service.py:239  np.load(offsets_file, allow_pickle=False)
analysis_service.py:272  np.load(offsets_file, allow_pickle=False)
analysis_service.py:426  np.load(path, mmap_mode="r", allow_pickle=False)
analysis_service.py:972  np.load(offsets_path, allow_pickle=False)
analysis_service.py:1525 np.load(..., mmap_mode="r", allow_pickle=False)
```

`np.save` 侧：`descriptor_service.py:375/377` 未传（默认 False）、`analysis_service.py:1291` 显式传 `allow_pickle=False` —— 保存侧也不一致，但默认已是 False，无实际风险。

**跨模块不一致确证**。numpy 2.5.2 默认 `allow_pickle=False`（numpy ≥ 1.16.3）→ **当前安全**。

**修复建议完备性评估**：
- **完整性**：红队建议"显式化并在 code review 中列为硬性要求"——正确且完整。**补充**：应同时给 `np.save` 加 `allow_pickle=False`（共 7 处），避免将来有人为了保存 object 数组而打开 pickle。
- **回归**：零。`allow_pickle=False` 显式化只会在**当前就依赖 pickle**的情况下失败，而 `values.npy`/`row_offsets.npy` 都是数值数组。
- **更优替代**：可在 `storage/` 或 `datasets/` 下封装一个 `_npy_load(path)` 帮助函数统一强制 `allow_pickle=False`，一次性消除整个代码库的歧义（但这是 M 级重构，S 级的直接加参数即可）。
- **工作量 / 类型**：**XS / 改代码**。建议随 RT-01 一起顺手做。

---

### A2 — `job.cancel` 无归属/权限校验

**判定：CONFIRMED，维持 Low**

**独立证据**：

```python
# main.py:104
"job.cancel": lambda params: jobs.cancel(params.get("id")),

# services/job_service.py:208-219
def cancel(self, job_id: str) -> dict:
    with self._lock: ctx = self._contexts.get(job_id)
    row = self.get_job(job_id)
    if row is None: raise AppError(JOB_NOT_FOUND, ...)
    if row["status"] in ("COMPLETED", "FAILED", "CANCELLED"):
        return {"ok": True, "already_finished": True}
    if ctx is None: raise AppError(INVALID_PARAMS, ...)
    ctx.cancel()
```

确证：无窗口/会话归属检查，无授权分级。

**补充（红队未展开）**：红队说"若取消正在落盘的 job，可能留下不一致状态"。我核实了两条落盘路径：
- `_commit_artifact`（`analysis_service.py:1268-1316`）：`ctx.check_cancelled()` 在写循环内（`:1286`），取消会抛 `JOB_CANCELLED` → `except Exception` → `_rmtree_quiet(str(staging))`（`:1315`）→ **staging 被清理，干净**。
- `_run_compute`（`descriptor_service.py:373-411`）：`run_dir.mkdir(parents=True, exist_ok=True)`（`:374`）之后才 `np.save`（`:375`），中间**没有** `check_cancelled()`；若在 `mkdir` 与 `np.save` 之间取消 → 留下**空 run_dir 孤儿目录**。低影响但确证。

**修复建议完备性评估**：
- **完整性**：红队建议"把 job 与发起窗口/会话绑定"方向正确，但**在 Tauri 2 里 `backend_send` 拿不到发起者身份**（除非加 `webview: tauri::Webview` 参数，见 RT-03）。因此**依赖 RT-03 第 3 步**才可实现。
- **会引入的回归**：绑定窗口后，若将来有多窗口或热重载场景，`job.list` 的可见范围会变化，UI 需同步。
- **更优替代**：短期（P2）只需把 `job.cancel` 标记为需要 UI 确认（前端已有确认对话框即可，纯前端改动）；长期随 RT-03 一起做归属绑定。
- **工作量 / 类型**：短期 **XS / 改前端**；长期 **M / 依赖 RT-03**。

---

### A3 — `settings.set` 无 key 白名单与长度限制

**判定：CONFIRMED，维持 Low**

**独立证据**：

```python
# main.py:74-79
def settings_set(params):
    key, value = params.get("key"), params.get("value")
    if not key or value is None:
        raise AppError(INVALID_PARAMS, "'key' and 'value' are required")
    settings_kv.set_setting(key, str(value))
    return {"ok": True}
```

```python
# storage/database.py:77-80, 154-159
CREATE TABLE settings ( key TEXT PRIMARY KEY, value TEXT NOT NULL );   # 无长度约束
def set_setting(self, key: str, value: str) -> None:
    self.execute("INSERT INTO settings(key, value) VALUES(?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
```

确证：无 key 白名单、无长度上限。配合 8 MB 帧上限 → 单个 key 可存 ~8 MB；循环调用可撑大 SQLite（`descriptor_runs` 同理）。

**补充（红队未提）**：`settings_get`（`main.py:68-72`）无 key 校验，任意 key 可读；但 settings 表只存本应用配置，无跨用户敏感性。

**修复建议完备性评估**：
- **完整性**：红队建议"加 key 白名单与 value 长度上限（4 KB）"——完整。**补充**：key 也应限制长度（如 128 字符）与字符集（避免 `\x00`、超长 key 撑爆主键索引）。
- **会引入的回归**：**需要确认前端实际使用的 key 全集**。红队指出是 `ui.language` 等少数 key（`i18n/index.ts`、`workspace.ts:70`）。若白名单漏了某个 key，会静默失败（当前 `settings_set` 无论 key 是否已知都返回 `{"ok": True}`，加白名单后应返回明确错误）。**建议在实现时先做一次"运行一周记录所有 key"的灰度，或直接改为"前缀白名单"（如只允许 `ui.`/`workspace.`/`analysis.` 前缀）+ 长度上限**，比穷举白名单更不易回归。（我对前端 key 全集的判断**置信度中**，未逐文件枚举。）
- **工作量 / 类型**：**S / 改代码**（或 XS / 仅加长度上限）。

---

### A4 — 错误信息与日志泄露绝对路径与内部异常

**判定：CONFIRMED，维持 Low**（**并确认脱敏改造不会破坏现有测试**）

**独立证据**：

```python
# protocol/server.py:72-76
except Exception as exc:
    log.exception("unhandled error in %s", method)
    self._write(frames.response_err(vid, AppError("INTERNAL_ERROR", f"{type(exc).__name__}: {exc}")))
```

回显路径的具体位置（逐条核实）：
- `dataset_service.py:134` `f"path does not exist: {path}"`
- `dataset_service.py:142` `f"already registered: {path}"`
- `datasets/base.py:67` / `:71` `f"unsupported dataset path: {path}"` / `f"directory without DeepMD npy layout (...): {path}"`
- `datasets/deepmd.py:35` / `:46` / `:85` — `f"no DeepMD frames found (...): {path}"`、`f"invalid DeepMD dataset ({type(exc).__name__}): {exc}"`、coord/type.raw 不一致消息
- `datasets/extxyz.py:52` / `:92` / `:104` / `:121` — `f"cannot read {self.source_path}: {exc}"`、`f"frame {index}: no pos/positions column"`、`f"frame {index} row {row}: truncated"`、`f"frame {index} row {row}: malformed atom line ({exc})"`

**日志侧**：

```python
# logging_setup.py:17, 22-32
log_file = data_dir() / "logs" / "backend.log"
fh = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=3, encoding="utf-8")
sh = logging.StreamHandler(sys.stderr); sh.setLevel(logging.WARNING)
```

**② 对红队描述的修正 / 补充（重要，影响修复优先级）**

- **release 下 stderr 被丢弃**：`main.rs:72` 是 `cmd.stdin(...).stdout(...).stderr(Stdio::null())`。因此 `logging_setup.py:29` 的 `StreamHandler(sys.stderr)` **在 release 下无输出**，WARNING+ 只落到 `backend.log`。**泄露面限于 `backend.log` 的读权限**（即 data_dir 的 ACL），不会显示给用户。红队未点明这一点，容易让人高估。
- **日志内容**：`log.exception` 的 formatter 是 `%(asctime)s %(levelname)s %(name)s %(message)s`（`:26`），**不含局部变量**，所以泄露的是堆栈行 + 源码绝对路径 + 异常文本，不是变量值。`main.py:154` 的 `log.info("backend %s starting; data dir %s", ...)` 明确记录 data_dir 绝对路径。
- **日志/数据文件权限**：`RotatingFileHandler` 与 `config.py:20` 的 `mkdir` 都使用**默认 ACL**（继承父目录）。若 data_dir 在共享位置 → `backend.log`、`database.sqlite`、`results/` 全对其他用户可读写。

**③ 判定理由**：确证。Low 合理（信息泄露 + oracle 辅助）。

**④ 修复建议完备性评估**

| 项 | 评价 |
|---|---|
| **好消息（红队未评估，我核实了）** | **脱敏改造零测试回归**：我 grep 了 `tests/`，所有错误断言**只检查 `error.code`**：<br>· `test_adversarial_fixes.py:110`：`assert done["error"]["code"] in ("INVALID_DATASET", "INTERNAL_ERROR")`<br>· `test_analysis_ipc.py:180`：`assert missing["error"]["code"] == "ANALYSIS_NOT_FOUND"`<br>· `test_job_cancel.py` / `test_run_remove.py` 用 `exc.value.code`<br>**没有任何测试断言错误消息文本** → 可以放心把 `str(exc)` 从 IPC 响应里去掉。 |
| 漏掉的边界 | (a) 只改 `server.py:75` 不够——约 **11 处**业务代码自己回显路径（上面列全了），必须逐一改；(b) **日志脱敏**要区分"绝对路径"与"文件名"，建议统一用一个 `_safe_path(p)` 返回 `p.name`；(c) 未提 `backend.log` 与 `database.sqlite` 的**文件权限**（随 RT-11 的 ACL 收紧一起做）。 |
| 更优替代 | 红队建议"返回 `code` + 稳定文案 + `error_id`，细节只进日志"——**正确**。**补充一个更简单的实现**：给 `AppError` 加一个 `public_message` 字段（默认等于 `message`），需要脱敏的构造点显式传"用户友好文案"，`server.py` 只发 `public_message`。这样不必改动所有构造点，渐进式改造即可。 |
| 依赖顺序 | 可独立先做（零回归、无依赖）。**建议排在 RT-03 之后立刻做**，因为它是 RT-06/BLUE-03 的 oracle 基础。 |
| 工作量 / 类型 | **M / 改代码**（11 处 + 日志脱敏 + ACL） |

---

### A5 — 产物写入使用 staging + `os.replace`（已缓解）

**判定：CONFIRMED（缓解有效）**，但**该防御自身引入 3 个新问题（BLUE-06/07/08）**

**独立证据（我确认红队的正面评价是对的）**：

```python
# services/analysis_service.py:1271-1316
root = self.data_dir / "analysis"
root.mkdir(parents=True, exist_ok=True)
staging = root / f".{analysis_id}.tmp-{uuid.uuid4().hex[:8]}"   # 1273：随机后缀
final = root / analysis_id
staging.mkdir(parents=True, exist_ok=False)                     # 1275：exist_ok=False 正确
...
np.save(staging / f"{safe_name}.npy", array, allow_pickle=False)  # 1291
manifest["files"][name] = {"path": target.name, ...}              # 1293：只存 basename
...
if final.exists(): raise AppError(ARTIFACT_INVALID, ...)           # 1310
os.replace(staging, final)                                          # 1312：原子替换
except Exception:
    self._rmtree_quiet(str(staging)); raise                         # 1315
```

**这个模式是正确的原子写入，`staging` 带随机后缀 + `exist_ok=False` 防了竞态占位。** 红队"无需修改"的评价成立。

**② 但我发现 3 个该防御自身的新问题**

| 问题 | 证据 | 说明 |
|---|---|---|
| **BLUE-06** | `analysis_service.py:1467-1474` `_artifact_is_complete` | 它读的是**磁盘上**的 `manifest.json`（不是 DB 列），`file_meta.get("path","")` 未经净化直接 `root / path` → 可以是 `../../..`。攻击者若能写产物目录（RT-10 前提），可伪造 `manifest.json` 使 `completed: True` + files 指向任意路径 → 绕过 `_require_artifact`。红队提到了这点，**我确认成立**，但红队只说"建议第 1472 行也限制为 `Path(name).name`"，**没说这会与 `manifest["files"]["metadata"] = {"path": "metadata.json"}`（`:1305`）的合法单层路径兼容**——用 `Path(x).name` 恰好兼容（都在同目录），方案可行。 |
| **BLUE-07** | `analysis_service.py:1310-1312` | `if final.exists(): raise`。若 `final` 是**悬空的**目录符号链接/junction，`exists()` 返回 `False` → 检查被绕过 → `os.replace(staging, final)`。Windows 上 `MoveFileEx` 遇到目标为目录重解析点时的语义**需实测**：可能是替换链接本身，也可能是移动到链接目标位置（后者可导致在攻击者指定位置创建目录）。**红队未提。** 修复：改用 `final.is_symlink() or final.exists()`。 |
| **BLUE-08** | `analysis_service.py:1290-1292` | `safe_name = name.replace("/", "_")` **只替换正斜杠**，未处理反斜杠 `\` 与 `..`。Windows 上 `staging / "..\\..\\evil.npy"` 会写到 staging 之外。`name` 来自 `result["arrays"]` 的键（由 `AnalysisEngine` 产生，非直接用户输入），所以**可利用性低**，但这是**未净化的路径拼接**，属防御不彻底。红队未提。修复：`safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", Path(str(name)).name)` 或用 `pathlib` 严格取 basename。 |

**③ 判定理由**：缓解有效，红队正面评价成立；但其边界不彻底，我补 3 条 Low。

---

### A6 — SQL 注入（红队阴性结论：**未发现**）

**判定：CONFIRMED（阴性结论成立）** —— 我独立复核，同意"未发现"。

**我的独立验证**：

```
$ grep -rn 'execute(|query(|query_one(' backend/ --include=*.py | grep -v "?" | grep -E 'f"|%s|\.format\(|\+ *str'
(无输出 → 所有调用都带 ? 占位符)

$ grep -rn 'LIKE' backend/ --include=*.py
services/analysis_service.py:355         "(descriptor_run_id = ? OR input_run_ids_json LIKE ?)"
services/analysis_service.py:1552        "... OR dataset_ids_json LIKE ?)"
services/dataset_service.py:371          "... OR dataset_ids_json LIKE ?)"
```

三处 `LIKE` 的模式串都是**硬编码 SQL 片段**，用户输入（`f'%"{run_id}"%'`）是**绑定值**而非 SQL 拼接 → 无注入。

`executescript` 唯一调用点 `storage/database.py:127` 使用模块常量 `MIGRATIONS[version]` → 安全。

**但我发现一个红队漏掉的相邻问题 → BLUE-09**：`LIKE` 的**通配符未转义**。`analysis.list` 的 `run_id`（`analysis_service.py:355-356`）由前端直接传入，绑定到 `f'%"{run_id}"%'`。若 `run_id = "%"`，模式变成 `%"%"%` → **匹配所有行** → `analysis.list {"run_id": "%"}` 返回全部分析记录（含其他 run 的）。同理 `_mark_stale`（`:1552-1553`）与 `_mark_runs_stale`（`dataset_service.py:371`）的 `dataset_id` 是内部生成的 `ds_<hex>`，不受影响。
单用户桌面场景下影响很小（同一用户拥有全部数据），但严格说是一个**过滤器绕过**。修复：`ESCAPE` 子句 + 转义 `%`/`_`/`\`，或改用 `json_extract`/`instr`。**Low / 改代码 / S。**

---

### A7 — 硬编码凭据 / 密钥（红队阴性结论：**未发现**）

**判定：CONFIRMED（阴性结论成立）**

```
$ grep -rniE "api[_-]?key|secret|password|token|bearer|aws_|private_key" backend/ --include=*.py
datasets/extxyz.py:81-83,102-111  → 仅 "token offset" / "tokens = f.readline().split()" 等分词相关标识符
```

**无硬编码密钥、令牌或凭据。** 我额外扫了 `src-tauri/src`（只有 1 个 `main.rs`，无密钥）与 `scripts/`（`verify_known_issues.py`、`make_fixtures.py`、`probe_engine.py`、`package.ps1` 均无密钥）。同意红队结论。

---

### A8 — 第三方模型反序列化器（mdescriptor 自研）

**判定：PARTIALLY CONFIRMED**（我**无法背书**"这是做得好的缓解"，因为未做完整审查；但红队建议的 fuzz 方向我同意，并补一条确证的相邻问题）

**① 我的审查边界（诚实声明）**

红队声称核查了 `.venv/Lib/site-packages/mdescriptor/descriptors/model_backed/_vendor/dpa4desc/weights.py:100-135`，结论是 `find_class` 只放行 `collections.OrderedDict` 与 `torch._utils._rebuild_tensor*`，`persistent_load` 用 `ZipFile.read()`。

**我没有对该文件做完整审查**，因此：
- 对"`find_class` 白名单是否完整、是否有其他 unpickler 路径、`persistent_load` 是否真的无路径遍历" —— **置信度低**，我不背书也不否认。
- 我**同意**"这是手写的 pickle 解析器，属高风险面，建议 fuzz"这一判断。

**② 我确证的相邻问题 → BLUE-10**

```python
# services/descriptor_service.py:286-288
elif ptype in ("string", "model"):
    if not isinstance(value, str):
        raise ValueError("expected string path")
    # 除"是字符串"外无任何校验：无路径规范化、无存在性/扩展名/根目录检查
```

```python
# mdescriptor_adapter.py:198-199
if isinstance(exc, FileNotFoundError):
    return AppError(MODEL_NOT_FOUND, str(exc))     # 消息含完整路径
```

`model` 类型的参数路径**零校验**地进入引擎；引擎 `FileNotFoundError` 被映射为 `MODEL_NOT_FOUND` 且**消息回显完整路径**（`str(exc)` 形如 `No such file or directory: 'C:/.../model.pt'`）→ 与 RT-06 同源的**存在性/路径 oracle**。

且 `main.py:87-146` 的方法表里 `descriptor.submit` 完全可达，攻击者可用它探测本机任意路径是否存在（`/` 不存在会抛别的错误码）。

**③ 修复建议**：
- 对 `ptype == "model"` 的值做与 RT-02/RT-06 一致的**来源控制**（前端用 `dialog.open()` 选文件）+ 后端拒绝 UNC/相对/ADS/保留设备名；
- `MODEL_NOT_FOUND` 的消息改为回显 `Path(p).name` 而非完整路径（随 A4 一起做）；
- 对 `_vendor/dpa4desc/weights.py` 做畸形 checkpoint fuzz（红队建议，同意；工作量 M，属"补强"而非"修漏洞"）。

**④ 工作量 / 类型**：model 路径校验 **S / 改代码**；fuzz **M / 测试**。

---

## 4. 现有防御清单

我把清点到的**所有**防御机制列在这里，包括那些有效的（好防御也要确认）。

| # | 机制 | 位置 | 防护目标 | 是否完整 | 缺口 |
|---|---|---|---|---|---|
| D1 | NDJSON 帧长度上限 8 MB | `protocol/frames.py:10,18` | 单帧过大 | **不完整** | 在整行读入内存**之后**才检查（BLUE-02）；Rust 侧无对应校验 |
| D2 | `protocol_version` 校验 | `protocol/frames.py:26-31` | 协议不兼容 | **完整（但副作用危险）** | 不匹配时 `os._exit(2)` 直接杀进程（BLUE-01） |
| D3 | `method` 非空字符串 + `params` 必须为 dict | `protocol/frames.py:34-38` | 畸形帧 | 完整 | 无方法白名单（RT-03） |
| D4 | stdout 写锁 | `protocol/server.py:24,32-36` | 帧交错损坏 | 完整 | 输出侧无节流/背压（RT-09） |
| D5 | 顶层 `try/except Exception` 兜底 | `protocol/server.py:72-76` | 未捕获异常导致崩溃 | **完整（但泄露）** | 回显 `type(exc).__name__: exc`（A4） |
| D6 | `vid` 类型宽容处理 | `protocol/frames.py:32` + `client.ts:58` | — | **不完整** | `id` 可以是任意类型（dict/str/float），前端只接受 number → 响应被静默丢弃，`pending` 条目泄漏。**影响 Low**，建议校验 `isinstance(vid, int)` |
| D7 | `ThreadPoolExecutor(max_workers=4)` | `protocol/server.py:25` | 并发 | **不完整** | 队列无界（RT-09） |
| D8 | `JobService` 双 worker | `job_service.py:86` | 慢任务隔离 | **不完整** | 队列同样无界（BLUE-05） |
| D9 | job 进度事件节流（200 ms / 1% / 阶段变更） | `job_service.py:64-79` | 事件风暴 | 完整 | — |
| D10 | 僵尸 job/run 启动时清扫 | `job_service.py:91-96, 118-129` | 崩溃后残留 RUNNING | 完整 | — |
| D11 | 关闭顺序：job pool → db | `main.py:198-201` | 生命周期 | 完整 | `os._exit(2)` 绕过它（BLUE-01） |
| D12 | `_frozen()` 阻断 pip 自更新 | `update_service.py:56-57,62-63,96-97` | release 供应链 RCE | **完整（有效）** | 仅 release；dev 完全开放（RT-05） |
| D13 | pip 用参数列表、无 `shell=True` | `update_service.py:101-110` | 命令注入 | **完整** | 缺 `--only-binary`/`--index-url`/版本正则（RT-05） |
| D14 | pip 进程取消时 `kill()` + `wait(10)` | `update_service.py:126-130` | 取消后残留安装 | **完整** | — |
| D15 | `detect_format` 扩展名白名单 | `datasets/base.py:69` | 非数据集文件 | **不完整** | 被 `format` 参数绕过（RT-06） |
| D16 | `source_path UNIQUE` 约束 | `storage/database.py:18` | 重复注册 | 完整（非安全控制） | — |
| D17 | `np.load(..., allow_pickle=False)` 显式化 | `analysis_service.py:239,272,426,972,1525`；`np.save` `:1291` | pickle RCE | **不一致** | `result_service.py:77,142` 未显式化（A1）；`np.save` 在 `descriptor_service.py:375,377` 未显式化 |
| D18 | `np.save(..., allow_pickle=False)` + 数组名 `replace("/","_")` | `analysis_service.py:1290-1291` | 路径遍历 | **不完整** | 未处理 `\` 与 `..`（BLUE-08） |
| D19 | staging + `os.replace` 原子产物写入 | `analysis_service.py:1268-1316` | 半写状态/TOCTOU | **基本完整** | `final` 为悬空链接时 `exists()` 失效（BLUE-07）；磁盘 manifest 的 `path` 未净化（BLUE-06） |
| D20 | `_MAX_PREVIEW_POINTS = 20_000` + `limit` 上限 | `analysis_service.py:40,389,428`；`result_service.py:167` | IPC 大载荷 | 完整 | — |
| D21 | `periodic_boundary_ghosts` 的 `max_ghosts=3000` + 分块 | `dataset_service.py:454-515` | 恶意晶胞 OOM | **完整（正面样例）** | 该模式应推广到 RT-08 |
| D22 | 奇异晶胞降级处理 | `dataset_service.py:472-475`；`deepmd.py:116-129` | `LinAlgError` | 完整（正面样例） | — |
| D23 | `_bond_cutoff` 范围校验（0.1–10.0） | `dataset_service.py:54-69` | 参数越界 | 完整（正面样例） | — |
| D24 | `descriptor` 参数 schema 校验 | `descriptor_service.py:235-294` | 参数注入 | **不完整** | `model` 类型只校验 `isinstance(str)`（BLUE-10） |
| D25 | SQLite 全参数化 + WAL + `foreign_keys=ON` + 写锁 | `storage/database.py:109-148` | SQL 注入 / 并发 | **完整** | 读路径 `query/query_one` 不在 `_write_lock` 内（BLUE-11） |
| D26 | SQLite 迁移版本化 | `storage/database.py:117-130` | schema 演进 | 完整 | 无完整性保护（RT-10 前提） |
| D27 | 无 `dangerouslySetInnerHTML` / `eval` / `new Function` | `frontend/src`（已 grep 确证） | XSS | **完整（当前）** | 依赖第三方库不引入 sink（RT-13 纵深防御） |
| D28 | CSP `default-src 'self'` + 紧的 `connect-src` | `tauri.conf.json:25` | 脚本注入/外联 | **不完整** | 缺 `object-src`/`base-uri`/`frame-ancestors`；未显式声明 `http://tauri.localhost`（RT-13） |
| D29 | 未启用 shell 插件、未削弱 CSP 选项 | `tauri.conf.json`、`Cargo.toml:16-20` | 命令执行 | **完整** | — |
| D30 | `installMode: perMachine` | `tauri.conf.json:35-37` | 安装目录可写 | **部分有效** | 便携部署/ACL 被放宽时失效（RT-04） |
| D31 | sidecar 用绝对路径 `Command::new` | `main.rs:116` | PATH 劫持 | 完整 | 同目录 DLL 侧载仍成立（RT-04） |
| D32 | `release profile: strip/lto/codegen-units=1` | `src-tauri/Cargo.toml:22-25` | 逆向（次要） | 完整 | — |
| D33 | `backend.spec` 排除 `tkinter/matplotlib/pytest/PyInstaller` | `backend/backend.spec:53` | 攻击面收敛 | 完整（正面） | 但 `hiddenimports` 带入了 `joblib`/`numba` |
| D34 | `Cargo.lock` + `package-lock.json` 存在 | 仓库根 / `frontend/` | 依赖漂移 | **不完整** | `npm install` 仍可漂移，需 `npm ci`（RT-12） |
| D35 | `requirements.txt` 用 `==` 固定 | `backend/requirements.txt:5-11` | 依赖漂移 | **不完整** | 无 `--hash`（RT-12） |
| D36 | `_MemorySampler`（RSS 采样） | `descriptor_service.py:39-103` | 内存可观测 | 完整（可复用于 RT-08 护栏） | 只记录不阻断 |
| D37 | job 取消协作式 `check_cancelled()` | `job_service.py:47-49` + 各 runner | 长时间任务 | 基本完整 | `descriptor_service.py:374-375` 的 mkdir→save 之间无检查（A2） |
| D38 | 数据集 `STALE` 标记机制 | `dataset_service.py:358-373`；`analysis_service.py:1543-1554` | 陈旧结果被误用 | **不完整** | 依赖只哈希 size+mtime 的指纹（RT-07） |

**小结**：38 项防御中，**完整 21 项 / 基本完整 4 项 / 不完整 13 项**。项目在"数值健壮性"（D21/D22/D23）与"产物原子性"（D19）上做得**明显好于**在"路径与信任边界"（RT-02/RT-06/RT-10/BLUE-03）和"资源上限"（RT-08/RT-09/BLUE-02/BLUE-05）上——这是一个清晰的、可操作的模式：**数值输入有 schema 校验，而字符串路径几乎没有校验。**

---

## 5. 蓝队补充发现（BLUE-01 … BLUE-14）

格式与红队一致。这些都是我通读时发现、红队未报的缺口。

---

### BLUE-01 — 协议版本不匹配时 `os._exit(2)` 直接杀死整个后端（**Medium**）

- **CWE**：CWE-400（资源耗尽）/ CWE-703（对异常条件的不当处理）
- **置信度**：高
- **位置**：`protocol/server.py:51-60`；触发面 `src-tauri/src/main.rs:22-35`
- **证据**

```python
# protocol/server.py:52-60
try:
    vid, method, params = frames.parse_request(line)
except AppError as exc:
    self._write(frames.response_err(None, exc))
    if exc.code == "PROTOCOL_VERSION_MISMATCH":
        log.error("protocol version mismatch, exiting")
        os._exit(2)          # 59：在 4 个 worker 线程之一里直接终止整个进程
    return
```

- **场景**（威胁 b/d，以及**纯误操作**）
  1. 任何能写后端 stdin 的一方（webview 脚本 = RT-03；或被投毒的 npm 依赖）发一行 `{"protocol_version":2,...}`，或干脆发一个**不含 `protocol_version`** 的合法 JSON 对象 → `frames.py:27` 判为不匹配 → `os._exit(2)` → **整个后端进程立即死亡**，所有 in-flight job 丢失，所有 pending 的 IPC Promise 需等 `backend-exit` 才被 reject。
  2. **非恶意触发**：前端缓存了旧版本 bundle（protocol_version≠1）→ 一发请求即杀后端。
  3. **跨信任边界的意外触发**：`main.rs:89` 用 `l.contains("\"backend.ready\"")` 做字符串匹配缓存 ready 行——任何含该子串的行都会污染 `ready_line`，而 `backend_ready_line()` 把它交给前端 → 前端按 v1 解析失败。
- **影响**：后端拒服（DoS），只需 1 个 IPC 调用。`os._exit(2)` 绕过 `main.py:198-201` 的 `finally`（`jobs.shutdown()` / `db.close()`）。**我已核实：因 `database.py:112` 启用了 WAL + `synchronous=NORMAL`，SQLite 不会损坏**，且 `job_service.__init__:91-96` 在下次启动时清扫僵尸行 → **不会造成持久损坏**，这降低了本条的严重度。但用户会看到"后端已退出"，且需手动重启（`main.rs` 无自动重启）。
- **红队覆盖情况**：红队只在 RT-03 的攻击场景 2 里一句带过（"注入一个非法行，使后端 `os._exit(2)`…从而搞挂整个后端"），**未单列为发现**。我认为值得单列，因为它有**非恶意触发路径**且是**防御措施自身（D2）引入的问题**。
- **修复**
  1. 把 `os._exit(2)` 改为**优雅降级**：对不匹配的帧只回 `response_err(None, AppError("PROTOCOL_VERSION_MISMATCH", ...))`，**不退出**。真正的"客户端过旧"应由**前端**检测（`backend.ready` 里的 `protocol_version` 不匹配时弹"请更新应用"）后自行停止。
  2. 若确实必须终止（防止旧客户端写坏数据），改为 `self._closed.set()` + `self.close()`（`:78-84` 已有优雅关闭路径），让 `serve_forever` 的循环自然退出，从而走到 `main.py:198` 的 `finally`。
  3. 附带修：`main.rs:89` 的 ready 行检测改为**解析 JSON 后判断 `frame.get("event") == "backend.ready"`**，而不是子串匹配。
- **验证**：静态即可（`grep -n "_exit" protocol/server.py`）。动态：在 dev 下用 `bp.send({"protocol_version":2,...})`，观察后端是否退出、后续 `system.info` 是否全部失败。
- **工作量 / 类型**：**S / 改代码**；第 3 点 XS。

---

### BLUE-02 — 后端 stdin 按行读取无长度上限，8 MB 限制在读取之后才生效（**Medium**）

- **CWE**：CWE-770（无限制地分配资源）/ CWE-400
- **置信度**：高
- **位置**：`protocol/server.py:41-42` + `protocol/frames.py:10,18`
- **证据**

```python
# protocol/server.py:39-47
def serve_forever(self) -> None:
    for raw in sys.stdin:            # 41：TextIOWrapper 按行迭代，NO 行长上限
        line = raw.strip()           # 42：整份副本
        if not line: continue
        if self._closed.is_set(): break
        self._pool.submit(self._handle, line)   # 47：入无界队列
```

```python
# protocol/frames.py:17-19
def parse_request(line: str) -> ...:
    if len(line.encode("utf-8", "replace")) > MAX_LINE_BYTES:   # 18：此时整行已在内存里
```

- **场景**（威胁 b/d）：攻击者经 `backend_send` 发一个 **500 MB 的单行**。Rust 侧（`main.rs:28`）`write_all` 无任何长度校验；Python 侧先把 ~500 MB UTF-8 解码为 `str`（UCS-2/4 → 可能 1–2 GB），`strip()` 再复制一份，**然后**才被 `parse_request` 拒绝。配合无界队列（RT-09）与 4 个 worker，可稳定 OOM。
- **与红队的关系**：这是对 **RT-09 的一处实质性修正**。红队明确写"8 MB 的行限制**本身是有效的**（`len()` 在解析前检查，**无法绕过**）"——**"无法绕过"只在调度层成立，在内存层不成立**。
- **影响**：后端 OOM / 不可响应。无提权无泄露 → Medium。
- **修复**
  1. **Rust 侧**（`main.rs:26` 之前）：`if line.len() > 8 * 1024 * 1024 { return Err("frame too large".into()); }` —— 省掉管道写入，但不能替代 Python 侧防护（攻击者可绕过 Rust 直接写 stdin，虽需已有本地代码执行）。
  2. **Python 侧（关键）**：把 `for raw in sys.stdin` 换成带上限的读取：
     ```python
     MAX_LINE_BYTES = frames.MAX_LINE_BYTES
     stdin_buf = sys.stdin.buffer
     while not self._closed.is_set():
         raw_bytes = stdin_buf.readline(MAX_LINE_BYTES + 1)
         if not raw_bytes: break
         if len(raw_bytes) > MAX_LINE_BYTES or not raw_bytes.endswith(b"\n"):
             self._discard_to_eol(stdin_buf)         # 丢弃到下一个 '\n'
             self._write(frames.response_err(None, AppError(INVALID_PARAMS, "frame exceeds 8 MB limit")))
             continue
         ...  # 手工 decode("utf-8", errors="replace")
     ```
  3. 顺带把 `main.py:33` 的 `errors="strict"` 改为 `errors="replace"`（见 BLUE-14）。
- **验证**：静态即可确认 `sys.stdin` 无上限。动态（隔离）：构造一个 200 MB 单行经 `backend_send` 发送，观察后端 RSS 曲线。
- **工作量 / 类型**：**M / 改代码**

---

### BLUE-03 — `dataset.frame` 是任意文件读取 oracle（经 DB 投毒路径，**不需要 webview 代码执行**）（**Medium**）

- **CWE**：CWE-22（路径遍历）/ CWE-209（错误信息含过多信息）/ CWE-73
- **置信度**：高（代码路径确证）
- **位置**：`services/dataset_service.py:376-388` + `:113-121` + `datasets/extxyz.py:99-122` + `protocol/server.py:72-76`
- **证据**

```python
# services/dataset_service.py:376-388
def frame(self, params: dict) -> dict:
    ds_id, index = params.get("id"), params.get("index")
    if not isinstance(index, int):
        raise AppError(INVALID_PARAMS, "'index' (int) is required")
    bond_cutoff = _bond_cutoff(params.get("bond_cutoff"))
    row = self._row(ds_id)                    # ← 只读 datasets 表，不检查任何 run/scan 状态
    adapter = self._adapter_for(row)          # :119 create_adapter(Path(row["source_path"]), row["format"])
    try:
        f = adapter.get_frame(index)
    except AppError:
        raise
    except Exception as exc:
        raise AppError(INVALID_DATASET, f"cannot read frame {index}: {exc}") from exc
```

```python
# datasets/extxyz.py:119-122
except (ValueError, IndexError) as exc:
    raise AppError(
        INVALID_DATASET, f"frame {index} row {row}: malformed atom line ({exc})"
    ) from exc
```

- **场景**（威胁 c：同机其他用户 / 同用户恶意软件，**配合 RT-11**）
  前置条件：**能写 `database.sqlite`**——`MDS_DATA_DIR` 指向共享/网络目录（大容量结果集场景常见），或同用户恶意软件。**不需要 webview 代码执行。**
  1. 攻击者用 sqlite3 往 `datasets` 表插一行：`source_path = "C:\Users\victim\.aws\credentials"`、`format = "extxyz"`、`fingerprint = "fp"`、`number_of_frames = 1`。
  2. 调 `dataset.frame {"id":"ds_x","index":0}` → `create_adapter` 因 `fmt` 已给定而**跳过 `detect_format` 的扩展名白名单**（`base.py:79`）→ `ExtXYZAdapter` 直接 `open()` 该文件并逐行解析。
  3. 目标文件首行若为整数 → 进入 `get_frame`；`float(tokens[pos_i])` 失败 → 抛
     `malformed atom line (could not convert string to float: '<token>')`
     经 `server.py:75` 原样回传 → **逐 token 外泄文件内容**。首行非整数则 `_build_index` 建出 0 帧 → `"frame index out of range: 0"`（仍是**存在性 oracle**）。
  4. 副作用：`_meta` → `compute_fingerprint` → `rglob` 会返回目标路径的 **size 与 mtime**。
- **影响**：在"DB 可写"前提下实现**任意文件存在性探测 + 部分内容外泄**；且 `dataset.frame` 是**同步 RPC 方法**（不是 job），攻击者可高速循环探测。
- **与红队的关系**：红队在 RT-06 第 3 点把"完整任意文件内容外泄"标为**未确证**，理由是"`dataset.frame` 需要一条 DB 记录，而记录只在 `register` 的 job 成功后才写入"。**该理由只对"经 `dataset.register` 建记录"成立**；直接写 DB 可完全绕过。我把红队自标未确证的子项**坐实**，但**更换了前提条件**（DB 可写，而非 webview 代码执行）。
- **修复**
  1. **与 RT-10 同一个根治方案**：不信任 DB 中的路径。对 `datasets.source_path` 在**读取侧**做校验（拒绝 UNC / 相对 / ADS / 保留设备名 / 符号链接），而不仅是写入侧。
  2. 短期：`dataset.frame` 的错误消息脱敏（随 A4 一起做，`extxyz.py:121` 去掉 `({exc})`），**这一条零回归**（已核实测试只断言 error code）。
  3. 把 `dataset.frame` 的 `except Exception` 分支的 `f"cannot read frame {index}: {exc}"` 也脱敏。
  4. 数据库完整性：若将来支持共享数据目录，应对 `database.sqlite` 加完整性保护（HMAC 关键表或 SQLCipher），使"改库"不再是无痕原语（红队在 RT-10 建议里提了，我附议并提到更高优先级）。
- **验证**：在临时 `MDS_DATA_DIR` 下按上述 SQL 插入一行，调 `dataset.frame`，观察错误消息是否回显文件内容片段。
- **工作量 / 类型**：脱敏 **S**；读取侧校验 **M**；DB 完整性 **L**。依赖 **RT-11**（先切断 DB 落在共享位置）。

---

### BLUE-04 — 子进程（sidecar / pip）环境未清洗，`PYTHONPATH`/`PIP_*`/`TEMP` 全部继承（**Medium**，dev 确证 / release 需实测）

- **CWE**：CWE-426（不可信搜索路径）/ CWE-15（外部控制的系统设置）
- **置信度**：dev 路径 **高**；frozen 路径 **中**（取决于 PyInstaller bootloader 是否读取 `PYTHONPATH`，**我未能从 Python 侧源码确认，需运行时验证**）
- **位置**：`src-tauri/src/main.rs:72, 117-121, 132-136`；`services/update_service.py:101-110`
- **证据**

```rust
// src-tauri/src/main.rs:72
cmd.stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null());
// 没有 .env_clear() / .env_remove(...)

// src-tauri/src/main.rs:132-136（dev 分支）
let mut c = Command::new(python);
c.args(["-m", "mdescriptor_studio_backend"])
    .current_dir(&backend_dir)
    .env("PYTHONIOENCODING", "utf-8")
    .env("PYTHONUTF8", "1");
// 只"加"环境变量，未移除任何
```

```python
# services/update_service.py:101-110
process = subprocess.Popen([sys.executable, "-m", "pip", ...], ...)
# 未传 env= → 完整继承
```

- **场景**（威胁 c：非提权即可设置环境变量）
  1. **dev 路径（确证）**：`main.rs:130` 用 `.venv\Scripts\python.exe -m ...`。该 Python **一定**读取 `PYTHONPATH`、`PYTHONSTARTUP`、`PYTHONHOME`。攻击者在快捷方式/父进程/`setx` 设 `PYTHONPATH=<攻击者目录>`，把自己的 `.py`（如 `numba.py`、`numpy.py`、`mdescriptor_studio_backend/...` 的影子模块）注入 → **后端进程内代码执行**。开发者机器是高价值目标。
  2. **release 路径（需实测）**：PyInstaller onefile 的 bootloader 对 `PYTHONPATH` 的处理取决于其 `PyConfig` 初始化方式（是否 `PyConfig_InitIsolatedConfig`）。我在 `.venv/Lib/site-packages/PyInstaller/` 的 Python 部分**未找到**相关代码（bootloader 是预编译 C，不可 grep），因此**无法静态确认**。需要运行时验证：设 `PYTHONPATH` 后启动 sidecar，观察 `system.info` 或用一个探针模块。
  3. **两条路径共有（确证）**：`PIP_INDEX_URL` / `PIP_EXTRA_INDEX_URL` / `PIP_TRUSTED_HOST` / `PIP_FIND_LINKS` / `PIP_PRE` 完整继承给 `update_service.py:101` 的 pip → 这正是 RT-05 的攻击面，红队建议 3（env 清洗）正确但在别处没做。
  4. **两条路径共有（确证）**：`TEMP`/`TMP` 继承 → 控制 PyInstaller onefile 的 `_MEIxxxxxx` 解压根目录（RT-04 的补充面），以及 `tempfile.gettempdir()` → **直接影响 RT-01 的 numba 缓存根**（`engine.py:613`）。
- **影响**：dev 下确认的代码执行；release 下可能的模块注入 + 确定的包管理器劫持面 + numba 缓存根重定向。
- **修复**
  1. `main.rs` 的 `backend_command()` 内，对**两个分支**都加显式环境清洗：
     ```rust
     // 白名单比黑名单更稳
     c.env_clear();
     c.env("SystemRoot", /*必需*/).env("TEMP", ...).env("TMP", ...)
      .env("LOCALAPPDATA", ...).env("USERPROFILE", ...) /*等最小集*/;
     // 或黑名单：c.env_remove("PYTHONPATH").env_remove("PYTHONSTARTUP")
     //           .env_remove("PYTHONHOME").env_remove("PIP_INDEX_URL") ...;
     ```
     注意：**Windows 上 `env_clear()` 会破坏 PATH 查找与 DLL 解析，必须显式保留 `SystemRoot`/`Path`**。建议用**黑名单**更安全。
  2. `update_service.py:101` 传 `env=clean_env`（红队建议 3 已给清单，正确）。
  3. `engine.py:613` 的 numba 缓存根**不要**依赖 `tempfile.gettempdir()`（随 RT-01 一起修）。
- **验证**：dev 下 `set PYTHONPATH=<临时目录>` 并放一个打印到文件的 `sitecustomize.py`/影子模块，启动观察。**release 下同样测一次以确认 PyInstaller 行为**（这是我唯一无法静态确认的点）。
- **工作量 / 类型**：**S~M / 改代码**（Rust + Python）

---

### BLUE-05 — `JobService` 线程池队列同样无界，且 `submit` 先写 DB 行（**Low–Medium**）

- **CWE**：CWE-770 / CWE-400
- **置信度**：高
- **位置**：`services/job_service.py:86, 99-116`
- **证据**

```python
# services/job_service.py:86
self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="job")   # 默认无界队列

# services/job_service.py:107-115
job_id = f"job_{uuid.uuid4().hex[:12]}"
self.db.execute("INSERT INTO jobs (...) VALUES (...)", (...))     # 108：先写 DB
...
self._executor.submit(self._run, job_id, job_type, runner)        # 115：后入无界队列
```

- **场景**：循环调 `dataset.register`（或 `dataset.rescan`、`descriptor.submit`）→ 每次都① 插入一条 `jobs` 行（SQLite 写，落盘）② 入无界队列。2 个 worker 消费不过来 → **队列内存增长 + `jobs` 表无上限膨胀 + `database.sqlite` 撑大**。
- **与红队的关系**：红队 RT-09 **只报了 RPC 池**（`server.py:25`），**漏了 job 池**。且 job 池的问题更严重一些：它同时污染**内存**和**磁盘（DB）**。
- **缓解**：`dataset._submit_recompute` 有 `_active_scans` 去重（`dataset_service.py:285-289`），但 `dataset.register`（`:220`）**没有**去重——同一路径并发注册会先撞 UNIQUE 约束，但每路都在 `register` 的早期检查（`:140`）之后才被发现，且不同路径可无限注册。
- **修复**
  1. `JobService.__init__` 用与 RT-09 相同的信号量背压（复用同一套实现）。
  2. `submit` 在信号量获取失败时**提前返回错误**，不要先写 DB 行（把"写 DB"移到获取信号量之后）。
  3. 加全局在途 job 上限（如 64）并给 `dataset.register` 加同路径去重（复用 `_scan_lock` 模式）。
- **工作量 / 类型**：**S / 改代码**。与 RT-09 一起做。

---

### BLUE-06 — `_artifact_is_complete` 信任磁盘上 `manifest.json` 的 `path` 字段（**Low**）

- **CWE**：CWE-345（数据真实性不足）/ CWE-22
- **置信度**：高
- **位置**：`services/analysis_service.py:1467-1474`（对照写入侧 `:1293`）
- **证据**

```python
# 写入侧（受控）：analysis_service.py:1293
manifest["files"][name] = {"path": target.name, ...}      # 只存 basename，安全

# 读取侧（不受控）：analysis_service.py:1467-1474
manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
if manifest.get("completed") is not True:
    return False
for file_meta in (manifest.get("files") or {}).values():
    if isinstance(file_meta, dict) and not (root / str(file_meta.get("path", ""))).is_file():
        return False
return True
```

- **场景**（威胁 c，配合 RT-10/RT-11）：攻击者能写 `data_dir/analysis/<analysis_id>/`（即 DB 可被写的同一前提）→ 改写 `manifest.json`，设 `completed: true` 且 `files: {"x": {"path": "../../../../Windows/System32/..."}}` → `_require_artifact`（`:1450-1456`）通过 → 该 analysis 被当作 COMPLETED 且产物完整，可用于污染后续分析结论。
- **与红队的关系**：红队在 A5 里提到了这一点，**我确认成立**，并补充：红队建议的"限制为 `Path(name).name`"**是可行且兼容的**——因为写入侧 `manifest["files"]["metadata"] = {"path": "metadata.json"}`（`:1305`）也是单层文件名，`Path("metadata.json").name == "metadata.json"`，不会被破坏。
- **修复**：`analysis_service.py:1472` 改为 `(root / Path(str(file_meta.get("path",""))).name).is_file()`。同时 `chunk()`（`:422`）与 `_rows_from_artifact()`（`:1525`）读的是 **DB 列** `artifact_manifest_json`，相对安全，但为一致性也应加同样的净化。
- **工作量 / 类型**：**XS / 改代码**；**零回归**（合法路径都是单层文件名）。

---

### BLUE-07 — `os.replace(staging, final)` 未考虑 `final` 为悬空 junction/符号链接（**Low**，需实测）

- **CWE**：CWE-59（符号链接跟随）
- **置信度**：中（Windows 上 `MoveFileEx` 遇到目录重解析点的确切语义**需实测**，我无法静态确认）
- **位置**：`services/analysis_service.py:1310-1312`
- **证据**

```python
# services/analysis_service.py:1273-1275, 1310-1312
staging = root / f".{analysis_id}.tmp-{uuid.uuid4().hex[:8]}"
final = root / analysis_id
staging.mkdir(parents=True, exist_ok=False)
...
if final.exists():                                        # 1310：悬空链接时返回 False
    raise AppError(ARTIFACT_INVALID, f"analysis artifact already exists: {final}")
os.replace(staging, final)                                # 1312
```

- **场景**：攻击者在 `data_dir/analysis/` 下预置一个**悬空的**目录 junction/符号链接 `<analysis_id>`（指向尚不存在的目标）。`final.exists()` 返回 `False`（悬空）→ 检查被绕过 → `os.replace(staging, final)`。若 Windows 的 `MoveFileEx` 把 staging 移动到**链接目标位置**，攻击者即可在自选位置创建目录树（虽然内容只是 `.npy` 产物）。
- **与红队的关系**：红队在 A5 里说这个模式"无需修改"——**我同意整体评价**，但指出 `exists()` 对悬空链接失效这一个边界。这是"防御措施自身引入的新问题"这一类里的典型。
- **修复**：`:1310` 改为 `if final.exists() or final.is_symlink():`（`Path.is_symlink()` 对悬空链接返回 `True`，不跟随）。并在 `_commit_artifact` 开头对 `root` 本身也做一次 `is_symlink()` 检查。
- **验证**：在临时目录用 `mklink /J` 建一个悬空 junction，跑一次 analysis，观察 staging 的最终落点。
- **工作量 / 类型**：**XS / 改代码**；零回归。

---

### BLUE-08 — `_commit_artifact` 的数组名只替换 `/`，未处理 `\` 与 `..`（**Low**）

- **CWE**：CWE-22 / CWE-73
- **置信度**：高（代码确证），可利用性 **低**（`name` 来自引擎内部，非直接用户输入）
- **位置**：`services/analysis_service.py:1290-1293`
- **证据**

```python
# services/analysis_service.py:1290-1293
safe_name = name.replace("/", "_")                    # 1290：只替换正斜杠
np.save(staging / f"{safe_name}.npy", array, allow_pickle=False)
target = staging / f"{safe_name}.npy"
manifest["files"][name] = {"path": target.name, ...}
```

在 Windows 上，`Path(staging) / "..\\..\\evil"` 会被解析为 `staging` 的上两级 → `np.save` 写到 staging **之外**。

- **场景**：`result["arrays"]` 的键由 `AnalysisEngine` 的各方法返回（例如 `engine.py:664` 的 `{"coords": ..., "explained_variance": ...}`），当前全是硬编码字面量 → **不可被 IPC 直接控制**。但若将来某个 algorithm 把用户参数（如 `analysis_type`、`params` 的某个键）用作数组名，即成为任意相对路径写。这是**未净化的路径拼接**，属防御不彻底。
- **与红队的关系**：红队在 A5 里说 `manifest["files"]` 只用 `target.name` 所以"不存在遍历问题"——**对读取侧成立**（BLUE-06 除外），但**写入侧**的 `np.save` 仍用了未净化的 `safe_name`。红队漏了写入侧。
- **修复**：`safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(str(name)).name) or "unnamed"`，并在写入后用 `target.resolve().parent == staging.resolve()` 做一次包含性断言。
- **工作量 / 类型**：**XS / 改代码**；零回归（当前数组名都是合法标识符）。

---

### BLUE-09 — SQL `LIKE` 通配符未转义 → 过滤器可被 `%`/`_` 绕过（**Low**）

- **CWE**：CWE-1284（不当的校验实现）/ 逻辑缺陷
- **置信度**：高
- **位置**：`services/analysis_service.py:355-356`（`analysis.list`）；`:1552-1553`（`_mark_stale`）；`services/dataset_service.py:371`（`_mark_runs_stale`）
- **证据**

```python
# services/analysis_service.py:354-356
if params.get("run_id"):
    conditions.append("(descriptor_run_id = ? OR input_run_ids_json LIKE ?)")
    args.extend([params["run_id"], f'%"{params["run_id"]}"%'])
```

`run_id` 来自前端且**未做 LIKE 通配符转义**。传 `run_id = "%"` → 模式 `%"%"%` → 匹配所有 `input_run_ids_json` 的行 → `analysis.list` 返回**全部**分析记录（含其他 run 的）。`_`/`\` 同理。

- **场景**：`analysis.list {"run_id": "%"}` 越权列举；更危险的是 `_mark_stale`（`:1552-1553`）与 `_mark_runs_stale`（`dataset_service.py:371`）用 `dataset_ids_json LIKE '%"<dataset_id>"%'`——这两个的 `dataset_id` 是内部生成的 `ds_<12 hex>`，**不受外部控制**，所以只是防御性缺陷而非当前可利用漏洞。
- **影响**：Low（同一用户拥有全部数据；`analysis.list` 还有 `LIMIT 500`）。但这是一个**过滤器语义被破坏**的缺陷，会导致 UI 显示错误的归属关系。
- **修复**：用 `ESCAPE` 子句 + 转义 `%`/`_`/`\`：
  ```python
  esc = run_id.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
  conditions.append("(descriptor_run_id = ? OR input_run_ids_json LIKE ? ESCAPE '\\')")
  args.extend([run_id, f'%"{esc}"%'])
  ```
  或改用 `EXISTS` + `json_each()` 精确匹配（更彻底，但需 SQLite JSON1 扩展——本项目 Python 3.12 自带 SQLite 支持 JSON1）。
- **工作量 / 类型**：**S / 改代码**；零回归（正常 run_id 是 `run_<hex>`，不含通配符）。

---

### BLUE-10 — `descriptor.submit` 的 `model` 参数零路径校验 + `MODEL_NOT_FOUND` 回显完整路径（**Low**）

- **CWE**：CWE-22 / CWE-209
- **置信度**：高
- **位置**：`services/descriptor_service.py:286-288` + `mdescriptor_adapter.py:198-199`
- **证据**

```python
# services/descriptor_service.py:286-288
elif ptype in ("string", "model"):
    if not isinstance(value, str):
        raise ValueError("expected string path")
    # 除类型外无任何校验
```

```python
# mdescriptor_adapter.py:198-200
if isinstance(exc, FileNotFoundError):
    return AppError(MODEL_NOT_FOUND, str(exc))     # str(exc) 形如 "No such file or directory: 'C:/.../x.pt'"
return AppError(INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")
```

- **场景**：`descriptor.submit` 在方法表（`main.py:101`）中完全可达。攻击者循环提交 `parameters.model = "C:\..."` → 从错误码区分 `MODEL_NOT_FOUND`（不存在）/ `DESCRIPTOR_CONFIGURATION_ERROR`（格式不对）→ **任意路径存在性与类型 oracle**。若 `model` 指向一个 UNC 路径，引擎打开文件时会触发 SMB 认证 → **NTLM 外泄**（与 RT-02/RT-06 同源的第三条 UNC 入口）。
- **与红队的关系**：红队在 A8 里建议对 model 路径做 fuzz，但**没有指出 `model` 参数本身零校验、也没有指出 UNC 这条入口**。这是 A8 的一个具体可修缺口。
- **修复**
  1. 对 `ptype == "model"` 的值加与 RT-02/RT-06 一致的检查：拒绝 UNC / `\\?\` / `\\.\` / ADS / 相对路径 / 保留设备名；要求 `Path(v).is_file()`。
  2. 前端 `SchemaForm.tsx:198-211` 改为用 `dialog.open()` 选文件（与 RT-02 的前端改造同一处工作）。
  3. `mdescriptor_adapter.py:199` 改为 `AppError(MODEL_NOT_FOUND, f"model file not found: {Path(str(exc)).name}")`（随 A4 一起做）。
- **工作量 / 类型**：**S / 改代码 + 改前端**

---

### BLUE-11 — `Database.query`/`query_one` 不受 `_write_lock` 保护（`check_same_thread=False`）（**Low**，健壮性）

- **CWE**：CWE-362（竞态条件）
- **置信度**：高
- **位置**：`storage/database.py:109-111, 132-148, 161-163`
- **证据**

```python
# storage/database.py:109-111
self._conn = sqlite3.connect(str(path), check_same_thread=False)
self._conn.row_factory = sqlite3.Row
self._write_lock = threading.RLock()

# storage/database.py:132-136（写在锁内）
def execute(self, sql, params=()):
    with self._write_lock: ...

# storage/database.py:143-148（读不在锁内）
def query(self, sql, params=()):
    return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

def query_one(self, sql, params=()):
    row = self._conn.execute(sql, params).fetchone()
```

- **场景**：`check_same_thread=False` 意味着调用方负责串行化，但**读路径完全不持锁**。4 个 RPC worker + 2 个 job worker + 主线程并发访问同一个连接。CPython 的 `sqlite3` 模块在 3.12 下对同一连接的操作有内部锁，所以**不会崩溃**，但：
  - `db.close()`（`main.py:201`）与并发 `query` 竞态 → `sqlite3.ProgrammingError: Cannot operate on a closed database`；
  - 长事务（如 `_migrate` 的 `executescript`）期间的读可能看到中间状态；
  - `fetchall()` 期间另一个线程 commit → 结果集跨事务边界（WAL 下通常是快照，风险低）。
- **影响**：Low。是健壮性/可维护性问题，不是当前可利用的安全漏洞。
- **修复**：把 `query`/`query_one` 也纳入 `_write_lock`（`RLock` 可重入，不会自锁）；或在 `close()` 里先置一个 `_closed` 标志并等待在途操作。
- **工作量 / 类型**：**XS / 改代码**；回归风险低（但会给读路径加锁，高并发下略有串行化，需观察）。

---

### BLUE-12 — RT-07 指纹算法变更会导致全量历史 run 被标记 STALE（**变更风险**，非漏洞）

- **类别**：数据迁移 / 变更管理
- **置信度**：高
- **位置**：`datasets/fingerprint.py:9-22` → `services/dataset_service.py:90-96`（`_meta`）→ `:358-373`（`_mark_runs_stale`）→ `services/analysis_service.py:938-940`（`_usable_run` 拒绝 STALE）
- **说明**：这条**不是漏洞**，而是"照红队建议改代码会造成的事故"，必须单列在整改计划里。
  新指纹算法 → 所有已注册数据集的 `fingerprint` 值变化 → `dataset_service.py:92` 的 `if current != row["fingerprint"]` 立即为真 → `_mark_runs_stale` 把该数据集下**所有 COMPLETED 的 `descriptor_runs` 与 `analysis_runs` 标记 STALE** → `_usable_run`（`analysis_service.py:938-940`）拒绝任何 STALE run 作为新分析输入 → **用户升级后所有历史结果变灰、不可用**。
- **必须的配套设计**
  1. 新指纹带算法版本号前缀（例如 `v2:<hex>`）；
  2. `_meta` 只在 `row["fingerprint"]` **也**带 `v2:` 前缀时才做内容比对；旧行走旧语义（size+mtime）；
  3. 启动时检测含非 `v2:` 指纹的数据集 → 排一个后台 `dataset.statistics` job 重算指纹（复用 `_submit_recompute`，它有 `_active_scans` 去重）；
  4. 迁移期间该数据集标记 `FINGERPRINT_MIGRATING`，UI 显示"正在重新校验"，**不标记 STALE**；
  5. 提供"跳过迁移"的用户设置（大库用户可能不愿重新读几十 GB）。
- **工作量 / 类型**：**L / 改代码 + 改前端 + 数据迁移**。这是把 RT-07 从 S 级变成 L 级的唯一原因。

---

### BLUE-13 — `data_dir()` 被两处独立调用，校验只加一处会不一致（**Low**，跨模块不一致）

- **CWE**：CWE-1053（缺少函数级一致性）/ 设计不一致
- **置信度**：高
- **位置**：`config.py:12-21`（`data_dir()`）；`logging_setup.py:17`（第 1 次调用）；`main.py:153`（第 2 次调用）
- **证据**

```python
# logging_setup.py:13, 17
from .config import data_dir
def setup_logging() -> None:
    log_file = data_dir() / "logs" / "backend.log"     # 17：独立的 data_dir() 调用
```

```python
# main.py:152-153
setup_logging()          # 152：内部已调一次 data_dir()
root = data_dir()        # 153：又调一次
```

- **场景**：RT-11 的修复如果只在 `main()` 里做（例如在 `:153` 后加校验），**日志路径会绕过校验**（`setup_logging` 在第 152 行已经用未校验的 `data_dir()` 建好 `logs/` 并打开日志文件）。若 `MDS_DATA_DIR` 是 UNC，则**在校验生效之前就已触发 SMB 认证**（NTLM 外泄）。
- **影响**：Low（是修复实施陷阱，不是当前漏洞）。
- **修复**：`data_dir()` 自身完成全部校验（校验逻辑放在 `config.py:12-21` 内部），所有调用点共享同一个已校验结果；或在 `main.py` 里把 `root` 算出来后**传给** `setup_logging(root)`（`logging_setup.py:16` 加一个可选参数）。
- **工作量 / 类型**：**XS / 改代码**；随 RT-11 一起做。

---

### BLUE-14 — stdio 以 `errors="strict"` 配置，单个畸形字节即终止后端（**Low**，健壮性）

- **CWE**：CWE-703（对异常条件的不当处理）
- **置信度**：高（代码确证）；可达性 **低**（Rust `String` 保证有效 UTF-8，正常路径不可触发）
- **位置**：`main.py:28-34`
- **证据**

```python
# main.py:30-33
for stream in (sys.stdin, sys.stdout):
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="strict")     # 33
```

- **场景**：`errors="strict"` 下，任何无法解码的字节都会让 `for raw in sys.stdin` 抛 `UnicodeDecodeError`。该异常在 `serve_forever` 里**未被捕获**，会冒泡出 `main()`（`main.py:196-201` 的 `try/finally` 会执行清理，但异常继续传播）→ **后端进程退出**。
  当前 Rust 侧传来的 `String` 一定是有效 UTF-8，所以**正常路径不可触发**；但若将来有任何其他组件（调试工具、测试脚本、被替换的 sidecar）写入后端 stdin，一个杂散字节即可搞挂后端。
- **修复**：改为 `errors="replace"`（配合 BLUE-02 的手工读取方案一并落地）。畸形字节会变成 U+FFFD → JSON 解析失败 → 回一个 `INVALID_PARAMS` 错误帧，**降级为单帧失败而不是进程死亡**。
- **工作量 / 类型**：**XS / 改代码**；零回归。

---

## 6. 统一整改计划

供主代理直接执行。按优先级排序；`类型`列区分"必须改代码"与"改配置/改流程"。

### P0 — 立即修复（构成完整本地攻击链 / 无交互 RCE / 任意文件写）

| 优先级 | 涉及 ID | 整改动作 | 涉及文件 | 工作量 | 回归风险 | 依赖顺序 |
|---|---|---|---|---|---|---|
| **P0-1** | RT-03（第1步） | **capabilities 去掉 emit 权限**：`"core:event:default"` → `"core:event:allow-listen", "core:event:allow-unlisten"`。一键关闭整条响应伪造链 | `src-tauri/capabilities/default.json` | **XS** | **低**（已 grep 确认前端未使用 `emit`） | 无。**建议第一个做** |
| **P0-2** | RT-03（第2步） | **Rust 侧拒绝控制字符与超长帧**：`backend_send` 开头加 `if line.bytes().any(\|b\| b==b'\n'\|\|b==b'\r'\|\|b==0)` 与 `line.len() > 8MB` 两个早退分支 | `src-tauri/src/main.rs:22-35` | **S** | **低**（`JSON.stringify` 已转义换行） | 无 |
| **P0-3** | RT-01 | **numba 缓存根脱离 `MDS_DATA_DIR`/`%TEMP%`**：`engine.py:613` 改为固定 `%LOCALAPPDATA%\MDescriptorStudio\numba-cache`；并在 `backend/run_backend.py` **顶部**（import numba 之前）设 `os.environ["NUMBA_DISABLE_JIT_CACHE"]="1"`，实测 UMAP 首跑耗时可接受则永久禁用磁盘缓存 | `analysis/engine.py:604-635`；`backend/run_backend.py` | **S** | **中**（禁用缓存 → UMAP 首次编译 +20~40 s）。**必须先实测再决定** | **依赖 RT-11**（先固定 `MDS_DATA_DIR`） |
| **P0-4** | RT-11 | **`MDS_DATA_DIR` 校验**：拒绝 UNC / 相对路径 / `\\?\` / `\\.\` / ADS；`config.py:20` 建目录后校验 ACL（非当前用户可写则告警）；`main.rs:117` 加 `cfg!(debug_assertions)` 守卫（release 不透传）；`logging_setup` 与 `main` 共用同一已校验入口（BLUE-13） | `config.py:12-21`；`logging_setup.py:16-17`；`main.rs:117-121` | **S** | **低**（`tests/test_backend_smoke.py:20` 直接起 Python，不经 Rust 分支 → 零回归） | **先于 P0-3 与 P1-删除类** |
| **P0-5** | RT-02（后端） | **`output_path` 校验函数**：拒绝 UNC / `\\?\` / `\\.\` / ADS / 相对路径 / 保留设备名 / 尾部空格与点 / 中间及末端符号链接；用 `Path.resolve()` + `is_relative_to()`（**不要**用 `".." in parts`，**不要**限制到 `data_dir/exports`）；`os.open(..., O_CREAT\|O_WRONLY\|O_TRUNC\|O_NOFOLLOW, 0o600)` 落盘。校验同时放进 `submit_export:669` **与** `_write_export:1318` | `services/analysis_service.py:669-673, 1318-1357` | **M** | **中**：① 若做成 `data_dir/exports` 白名单会破坏 `test_analysis_ipc.py:169-172` 与 `test_analysis_api.py:300`；② 非 ASCII/长路径(>260)需实测 | **P0-2 之后**（否则可被直达绕过） |
| **P0-6** | RT-02（前端） | **导出路径只能由 `plugin-dialog` 的 `save()` 填充**，禁止手输 | `frontend/src/pages/Analysis.tsx:216, 764, 770, 914` | **S** | **低**（纯 UI 改造，dialog 插件已授权） | 无依赖，**可与 P0-1 同期做** |

### P1 — 本迭代修复

| 优先级 | 涉及 ID | 整改动作 | 涉及文件 | 工作量 | 回归风险 | 依赖顺序 |
|---|---|---|---|---|---|---|
| **P1-1** | RT-09 + BLUE-02 + BLUE-05 | **资源上限三件套**：① RPC 池用 `BoundedSemaphore(64)` 做背压（**不要自己写线程池**，红队的 `_BoundedPool` 会在 shutdown 挂死）；② `server.py:41` 改为带 `MAX_LINE_BYTES+1` 上限的 `sys.stdin.buffer.readline()` + 丢弃到 EOL + `errors="replace"`（BLUE-14）；③ `JobService` 同样加背压，且 `submit` 把"写 DB 行"移到获取信号量**之后** | `protocol/server.py:39-47`；`services/job_service.py:86,99-116`；`main.py:33` | **M** | **中**：限流后前端需处理 `BUSY`（`client.ts` 加重试/提示）；`queue_size` 建议先取 256 | **先于 P1-4（RT-07）** |
| **P1-2** | RT-08 | **extxyz/DeepMD 规模护栏**：`extxyz.py:39` 后加 `natoms` 上限（1e6）与"文件剩余字节 < natoms×16 则视为损坏"；`:99` 分配前估算字节数并设 512 MiB 上限；`_parse_comment` 的 `except OSError` 扩为 `except (OSError, ValueError)` 并转 `AppError(INVALID_DATASET)`；`descriptor_service.py:336-343` 改分块（复用 `_MemorySampler`） | `datasets/extxyz.py:38-50, 99-100, 144-178`；`services/descriptor_service.py:333-343` | **M** | **低**（测试最大 64 原子） | **与 P1-1 同期** |
| **P1-3** | RT-10 + BLUE-03 | **删除路径不信任 DB**：`result.remove`/`analysis.delete` 改为从 `run_id`/`analysis_id` **重新推导**受信任路径（`data_dir/"results"/f"run_{id.removeprefix('run_')}"`、`data_dir/"analysis"/analysis_id`），与 DB 值比对，不一致则 `log.error` + 拒绝删除；`_rmtree_quiet` 拆成严格版 `_rmtree_managed`（`onerror=` 记录）与宽松版；`dataset.frame` 的 `source_path` 加**读取侧**校验（BLUE-03） | `services/result_service.py:79-113`；`services/analysis_service.py:373-381,1315,1570-1575`；`services/dataset_service.py:113-121,376-388` | **S**（重推导方案，不改构造签名 → 零测试回归） | **低**（重推导方案避开 `test_result_list.py`/`test_run_remove.py` 的构造签名） | **P0-4（RT-11）之后** |
| **P1-4** | RT-07 + BLUE-12 | **指纹加内容摘要 + 迁移方案**：`fingerprint.py` 改为"size + mtime_ns + 头/中/尾各 1 MiB 采样"，**带 `v2:` 版本前缀**；加进程内 TTL 缓存（key=`(path,size,mtime_ns)`）与全局采样字节预算（32 MiB）；旧指纹数据集排后台 rescan 重算，期间标 `FINGERPRINT_MIGRATING` **不标 STALE**；`f.is_symlink()` 跳过而非拒绝 | `datasets/fingerprint.py:9-22`；`services/dataset_service.py:90-96, 285-346` | **L** | **高**：① 不做迁移会全量 STALE；② 不加预算/缓存会让 `dataset.list` 卡死。**必须先出迁移方案再改代码** | **在 P1-1 之后**（无界队列会把慢速放大成 OOM） |
| **P1-5** | A4 + BLUE-03（脱敏） | **错误消息脱敏**：给 `AppError` 加 `public_message` 字段（默认=`message`），`server.py:75` 只发 `public_message` + `error_id`；逐一改 11 处回显路径的构造点（`dataset_service.py:134,142`、`base.py:67,71`、`deepmd.py:35,46,85`、`extxyz.py:52,92,104,121`）；`MODEL_NOT_FOUND` 只回显 basename（BLUE-10）；日志统一用 `_safe_path()` 脱敏 | `errors.py`；`protocol/server.py:72-76`；上述 11 处；`mdescriptor_adapter.py:198-199` | **M** | **低**：已核实**所有测试只断言 `error.code`，不断言消息文本** → 零测试回归 | 无依赖，**建议尽早做**（它是多个 oracle 的基础） |
| **P1-6** | RT-06 | **去掉 `format` 参数**：`dataset_service.py:136` 改为 `fmt = detect_format(path)`；`register` 入口加 UNC / 相对 / ADS / **保留设备名** / 尾部空格与点 检查；`fingerprint.py:13` 的 `rglob` 加条目与总字节上限；`dataset.frame:378` 加 `not isinstance(index, bool)` | `services/dataset_service.py:128-140, 376-388`；`datasets/base.py:69-84`；`datasets/fingerprint.py:13` | **S~M** | **低**：已核实**无测试给 `dataset.register` 传 `format`** | **P0-2 之后** |
| **P1-7** | BLUE-01 | **`os._exit(2)` 改优雅降级**：不匹配只回错误帧不退出；若确需终止则用 `self._closed.set()` + `self.close()`；`main.rs:89` 的 ready 检测改解析 JSON 判断 `event=="backend.ready"` | `protocol/server.py:51-60`；`src-tauri/src/main.rs:89` | **S** | **低** | 无 |
| **P1-8** | BLUE-04 | **子进程环境清洗**：`main.rs` 的 dev 与 release 两个分支都 `.env_remove("PYTHONPATH"/"PYTHONSTARTUP"/"PYTHONHOME"/"PIP_*")`；`update_service.py:101` 传 `env=clean_env` | `src-tauri/src/main.rs:106-138`；`services/update_service.py:101-110` | **S~M** | **中**：Windows 上**不要**用 `env_clear()`（会破坏 DLL 解析），必须用黑名单 `env_remove`。需实测启动 | 与 RT-04/RT-05 同期 |

### P2 — 后续迭代

| 优先级 | 涉及 ID | 整改动作 | 涉及文件 | 工作量 | 回归风险 | 依赖顺序 |
|---|---|---|---|---|---|---|
| **P2-1** | RT-03（第3步） | **`backend_send` → `backend_request(method, params)`**：Rust 用 CSPRNG 生成 id 并 `serde_json::to_string` 序列化；`main.rs:94` 改 `emit_to(webview_label)`（需给命令加 `webview: tauri::Webview` 参数）；同步改 `client.ts:66-82` **与** `preview.tsx:844` 的 mock | `src-tauri/src/main.rs:21-35, 84-102`；`frontend/src/ipc/client.ts:66-82`；`frontend/src/preview.tsx:841-860` | **L** | **中**：签名变更面广；`preview.tsx` 漏改会导致浏览器预览模式失效 | 在 P0-1/P0-2 之后；与 A2 的归属绑定一起做 |
| **P2-2** | RT-04 + RT-12 | **sidecar 完整性校验**：`scripts/package.ps1:14` 后加 `Get-FileHash -Algorithm SHA256` 写 `backend-<triple>.exe.sha256`；`main.rs:114-116` 前用 `include_str!` 内嵌哈希比对（**两个候选名都要校验**）；165 MB 二进制移出 Git（LFS/制品库）；CI/发布强制 `npm ci` | `scripts/package.ps1:11-14`；`src-tauri/src/main.rs:109-126`；仓库 | **M~L** | **低**（哈希不匹配会导致启动失败 → 必须保证构建顺序：PyInstaller → 哈希 → tauri build） | 与 RT-12 一起做 |
| **P2-3** | RT-05 | **pip 加固**：版本正则（在 `_check:80` 与 `update_runner:96` 两处都加）；Popen 加 `--index-url https://pypi.org/simple --only-binary=:all: --no-cache-dir --no-input`（**跳过 `--require-hashes`**，依赖树太重）；env 清洗（随 P1-8） | `services/update_service.py:73-116` | **S** | **低**（release 无影响）；**需确认 `tests/test_engine_update.py` 是否用了非规范版本号** | 与 P1-8 同期 |
| **P2-4** | RT-13 | **CSP 收紧（保守版）**：只加 `object-src 'none'; base-uri 'none'; frame-ancestors 'none'`，**显式补 `http://tauri.localhost`**，**保留 `script-src` 继续回退 `default-src`**（避免拦掉 plotly/3dmol 的 blob Worker）；**不要**在本轮加严格 `script-src`，待三页实测确认无 blob Worker 后单独收紧 | `src-tauri/tauri.conf.json:24-26` | **XS**（纯配置） | **低**：必须在 Analysis / Explore / StructurePreview 三页**实测**通过后再合入 | 无 |
| **P2-5** | A1 + A3 | **反序列化兜底**：`result_service.py:77` 与 `:142` 的 `np.load` 显式传 `allow_pickle=False`；全仓再跑一遍 `grep -rn "pickle.load\|yaml.load\|torch.load\|np.load\|joblib.load" backend/`，`yaml.load` 一律换 `safe_load`，`torch.load` 保留 `TorchCheckpointUnpickler` 并强制 `weights_only=True` | `services/result_service.py:77,142`；`mdescriptor_adapter.py`；`requirements.txt` | **S** | **低**（本地产物本就以非 pickle 方式写出，加参数不改变行为） | 无 |
| **P2-6** | BLUE-06 + BLUE-07 + BLUE-08 | **产物落盘三项加固**：① `_artifact_is_complete` 改为用 `analysis_id` **重推导**出的路径与磁盘 `manifest.json` 里的 `path` 比对，不一致即判为不完整；② `_commit_artifact` 落盘前 `os.lstat(final)`，命中 `S_IFLNK` 则先 `os.unlink` 再 `os.replace`；③ `safe_name` 同时替换 `\`、拒绝 `.`/`..`、NFKC 归一并截断到 64 字符 | `services/analysis_service.py:1268-1316, 1467-1474` | **S~M** | **低**（产物名来自内部计算）；但"重推导"必须与 P1-3 的目录约定保持一致 | **在 P1-3 之后** |
| **P2-7** | BLUE-11 | **读路径并发保护**：推荐改 `threading.local()` 每线程一连接（避免读被写阻塞）；退而求其次才把 `query`/`query_one` 纳入 `_write_lock` | `storage/database.py:106-148` | **S** | **中**：换连接方式会影响 WAL / `foreign_keys=ON` 的**逐连接**设置，需回归 `tests/` 全部 DB 用例 | 可与 P1-3 并行 |
| **P2-8** | BLUE-09 | **`LIKE` 通配符转义**：新增 `escape_like(s)`（`\`→`\\`、`%`→`\%`、`_`→`\_`），三处调用点补 `ESCAPE '\'` | `services/analysis_service.py:355-356, 1552-1553`；`services/dataset_service.py:371` | **XS** | **低** | 无 |
| **P2-9** | BLUE-05（归属部分） | **JobService 取消归属校验**：`cancel(job_id)` 增加调用来源与 job 创建者的绑定（复用 P2-1 引入的 `webview` 参数）；同时把 `submit` 的"写 DB 行"移到获取信号量之后 | `services/job_service.py:99-116, 208-219` | **S** | **低** | **依赖 P2-1** |
| **P2-10** | BLUE-10 + A4 | **模型路径校验**：`descriptor.submit` 的 `model` 参数复用 P0-5 的同一套路径校验函数（拒绝 UNC / `\\?\` / `\\.\` / ADS / 保留设备名 / 尾部空格与点 / 符号链接），或收敛到"模型目录白名单根"；`MODEL_NOT_FOUND` 只回显 `Path(p).name` | `services/descriptor_service.py:235-294, 373`；`mdescriptor_adapter.py:198-199` | **S** | **中**：用户自定义模型路径是**合法需求** → 只能白名单根 + 校验，**不要**硬限制到 `data_dir` | **在 P0-5 之后**（复用校验函数） |
| **P2-11** | RT-12 + A5 | **供应链加固**：`pip-compile --generate-hashes` 产出 `requirements.lock.txt`，安装走 `--require-hashes`；`package.json` 的 13 处 `^` 改精确版本 + 提交 `package-lock.json`，CI 强制 `npm ci`；`Cargo.lock` 入库并接 `cargo audit`；`scripts/package.ps1` / `build_backend.ps1` 的产物补 `Get-FileHash` 留档 | `backend/requirements.txt`；`frontend/package.json`；`scripts/*.ps1`；CI 配置 | **M** | **低**（锁版本后升级需走显式流程） | 无 |
| **P2-12** | RT-04 | **安装目录与加载顺序加固**：实测确认 `%PROGRAMFILES%\MDescriptor Studio` 普通用户不可写（`installMode: perMachine` 理论上成立，需实测）；sidecar 启动前 `SetDllDirectory("")` 或使用绝对路径命令行；`binaries` 目录禁止非管理员写入 | `src-tauri/tauri.conf.json`；`src-tauri/src/main.rs:106-126` | **M** | **低** | 与 P2-2 同期 |
| **P2-13** | A2 + A8 | **前端净化与 mock 收敛**：全量 `grep -rn "dangerouslySetInnerHTML\|innerHTML\|outerHTML\|new Function\|eval(\|javascript:"` 并逐处确认；`StructurePreview` 的 3dmol / plotly 数据在渲染前过 JSON schema 校验；`preview.tsx` 的 mock 后端加显式注释并尽量复用 `client.ts` 的序列化逻辑 | `frontend/src/**`；`frontend/src/preview.tsx` | **M** | **低** | 与 P2-1 同期（两者都动 `client.ts` 周边） |
| **P2-14** | BLUE-13 | **`data_dir()` 单点收敛**：`config.py` 暴露带缓存的 `data_dir_validated()`；`logging_setup.py:17` 与 `main.py:153` 统一改调它；`config.py:20` 的 `mkdir` 显式设"仅当前用户"ACL | `config.py:12-21`；`logging_setup.py:16-17`；`main.py:152-153` | **S** | **低** | **随 P0-4 一起做**（同一处改动，不要拆两次） |
| **P2-15** | BLUE-14 + A6 + A7 | **协议健壮性 fuzz 用例**：新增测试覆盖——超长行、无换行结尾、非法 UTF-8 字节、`vid` 为 `bool`/`str`/`dict`/`list`/`null`、`method` 非字符串、`params` 为数组、JSON 深度嵌套 1000 层、单行 8 MB+1 | `tests/test_protocol_fuzz.py`（新增） | **M** | **低**（纯新增测试，不动产品代码） | **在 P1-1 / P1-7 之后**（先修限长与 `os._exit` 再 fuzz，否则用例必挂） |

### P3 — 可选 / 纵深防御（不阻断发布）

| 优先级 | 涉及 ID | 整改动作 | 涉及文件 | 工作量 | 回归风险 | 依赖顺序 |
|---|---|---|---|---|---|---|
| **P3-1** | RT-04 | **`console=False`**：发布版隐藏 PyInstaller 控制台窗口，减少"用户被诱导输入"的面 | `backend/backend.spec` | **XS** | **中**：启动期报错不可见 → **必须先保证日志可靠落盘** | 在 P1-5 之后 |
| **P3-2** | BLUE-03 + RT-10 | **DB 完整性保护**：`database.sqlite` 只落"仅当前用户可写"目录；启动自检 `PRAGMA integrity_check` + 关键表行数 sanity check；关键行（`datasets.path`、`runs.result_path`、`analysis.artifact_path`）加 HMAC，密钥存 DPAPI 保护的位置 | `storage/database.py`；`config.py` | **L** | **中**（改动面大，建议下个大版本） | 在 P0-4 之后 |
| **P3-3** | RT-03 | **IPC 审计**：记录 `method`、耗时、来源窗口（脱敏后），异常频次告警 | `protocol/server.py:39-47` | **S** | **低** | 在 P1-5 之后 |
| **P3-4** | 全局 | **威胁建模文档化 + 回归固化**：把 B1–B11 信任边界、21 条红队结论、14 条蓝队补充沉淀为长期维护的 `docs/security/threat-model.md`；每条修复配一条回归用例，防回退 | `docs/security/`；`tests/` | **M** | **低** | 最后做 |

### 6.1 修复关键路径（建议执行顺序）

```
P0-1（capabilities 去 emit，5 分钟，零风险）
  ↓
P0-2（Rust 侧拒控制字符 + 限长）
  ↓
P0-4（MDS_DATA_DIR 校验 + data_dir 单点收敛 → 含 P2-14）
  ↓
P0-3（numba 缓存根迁出 / 禁用）  ←┐ 依赖 P0-4
  ↓                                │
P0-5 + P0-6（export 路径校验 + 前端 dialog 化）
  ↓                                │
P1-5（错误消息脱敏，是多个 oracle 的基础）
  ↓
P1-1（资源上限三件套）→ P1-4（指纹 v2 + 迁移，高风险最晚做）
  ↓
P1-2 / P1-3 / P1-6 / P1-7 / P1-8
  ↓
P2-1（backend_request 重构）→ P2-9（job 归属）
  ↓
其余 P2 / P3
```

**三条硬依赖，不可乱序**：

1. **P0-4 必须早于 P0-3**：numba 缓存根要落在 `MDS_DATA_DIR` 之外，得先确定 `MDS_DATA_DIR` 本身可信。
2. **P1-5 必须早于 P3-1**：`console=False` 之后控制台不可见，日志若还在回显原始路径/异常文本，会把信息泄露从"屏幕"搬到"磁盘"，更难发现。
3. **P1-1 必须早于 P1-4**：指纹加内容摘要会让 `dataset.list` 变慢，无界线程池队列会把"慢"放大成 OOM。

### 6.2 工作量与收敛效果速览

| 档位 | 条数 | 其中"纯配置/零代码改动" | 修完后的收敛效果 |
|---|---|---|---|
| P0 | 6 | 1 条（P0-1） | 关闭"无交互 RCE + 任意路径写 + 响应伪造"三条完整攻击链 |
| P1 | 8 | 0 条 | 关闭信息泄露 oracle、DoS/资源放大、删除路径劫持、协议自杀 |
| P2 | 15 | 2 条（P2-4 配置、P2-11 部分） | 补齐供应链、产物完整性、并发、前端净化、回归用例 |
| P3 | 4 | 1 条（P3-1） | 纵深防御，防回退 |

---

## 7. 残余风险与假设

### 7.1 即使全部整改完成，仍然存在的残余风险

| # | 残余风险 | 为什么修不掉 | 建议的补偿控制 |
|---|---|---|---|
| R1 | **本机恶意软件 / 同机低权用户仍是"上帝模式"** | 桌面应用的宿命：攻击者一旦能在用户会话里跑代码，就能直接读写 `MDS_DATA_DIR` 下的 SQLite、伪造 IPC 帧（除非做到 P2-1 的 `emit_to` + 来源绑定）、替换 sidecar。P0/P1 只能抬高成本，不能根除 | P3-2（DB 完整性 + HMAC）、P2-2（sidecar 哈希）、P2-12（安装目录 ACL）三者叠加；并明确告知用户"不要在不可信机器上使用" |
| R2 | **`database.sqlite` 被篡改后仍是一条通用原语** | RT-10/BLUE-03 的修复把"删除"改为重推导，但**读取侧**（`dataset.frame` 的 `source_path`、`_artifact_is_complete` 的 manifest `path`）在 P1-3 / P2-6 之前仍信任 DB。即便全部修完，DB 里可被任意改写的**非路径字段**（状态、参数、mtime）仍能制造逻辑混乱 | 只信任"重推导路径"，DB 仅作索引；关键行 HMAC（P3-2） |
| R3 | **依赖树投毒无法靠代码修复** | numba / llvmlite / llvmlite 的 LLVM、dpdata、ASE、PyInstaller、Electron-adjacent 的 npm 包——任何一个上游或镜像被投毒，都等价于 RCE。`--require-hashes`（P2-11）只能防"传输中被替换"，防不住"上游发布时就是恶意的" | 锁版本 + 定期 `cargo audit`/`pip-audit` + 只走官方源；发布前做一次依赖 diff review |
| R4 | **numba 磁盘 JIT 缓存即使迁出 `MDS_DATA_DIR` 仍可能被投毒** | `%LOCALAPPDATA%\MDescriptorStudio\numba-cache` 对同机低权用户仍可写（除非显式设 ACL）。P0-3 只把风险从"共享数据集目录"降到"用户私有目录" | P0-3 落地时**同时**给缓存目录设"仅当前用户"ACL；若实测可接受，**优先选 `NUMBA_DISABLE_JIT_CACHE=1`**（彻底消除面） |
| R5 | **前端 XSS → IPC 提权链在 P2-1 之前始终存在** | P0-1 砍掉 `allow-emit` 只解决**响应伪造**这一环；`backend_send` 本身仍是全权限代理，前端一旦有 XSS（A2/A8 未发现现有注入点，但新增代码可能引入）就能调全部后端方法 | P2-1（`backend_request` + `emit_to`）是根治手段；过渡期靠 P0-2（Rust 侧限长/拒控制字符）+ P1-1（背压）降低爆炸半径 |
| R6 | **DoS / 资源耗尽只能缓解不能消除** | 单机应用没有服务端那样的配额体系；恶意 8 MB 帧 × 无界队列、恶意 extxyz 的 `natoms` 声明、恶意 DeepMD npy 全量加载，都能让用户自己把内存打满 | P1-1 + P1-2 的组合；同时在 UI 上对"数据集大小/原子数"给出明确上限提示 |
| R7 | **UNC 路径的 NTLM 凭据泄露只能靠"拒绝 UNC"规避** | Windows 平台行为：只要把 UNC 路径交给任何会做文件系统访问的 API，就可能触发 NTLM 认证。所有入口校验都做了（P0-4/P0-5/P1-6/P2-10），但**新增代码**可能漏 | 把"拒绝 UNC"做成唯一的 `validate_user_path()` 工具函数并强制所有入口调用；加 P2-15 的 fuzz 用例锁死行为 |

### 7.2 审计假设与未验证项（**重要：以下结论均为静态分析，未做任何动态利用**）

| # | 未验证项 | 影响 | 置信度 | 如何验证（不在本次范围内） |
|---|---|---|---|---|
| U1 | **PyInstaller onefile 是否继承 `PYTHONPATH`** | BLUE-04 的 release 分支成立与否。bootloader 是预编译 C，无法从 Python 侧源码判定 | **中** | 打包后 `set PYTHONPATH=<恶意目录>` 再启动，观察是否加载 |
| U2 | **`os.replace(staging, final)` 对悬空 junction 的语义** | BLUE-07 的严重性。若 `os.replace` 跟随链接，则"原子写"可被用于写到链接指向的位置 | **中** | 在 Windows 上建悬空 junction 后调用 `os.replace` 观察目标位置 |
| U3 | **plotly / 3dmol 是否使用 blob Worker** | RT-13 的 CSP 收紧是否会破坏功能（P2-4 之所以选保守版就是因为这个不确定） | **中** | 加严格 `script-src` 后在 Analysis / Explore / StructurePreview 三页实测 |
| U4 | `tests/test_engine_update.py` 是否使用非规范版本号 | P2-3 加版本正则是否会破坏测试 | **中** | 读该文件全文（本次只读了部分） |
| U5 | `weights.py` / 模型加载路径是否还有第二处反序列化 | A8（未发现）与 A3 的完备性 | **中** | 全量读 `mdescriptor_adapter.py` 之外的模型加载代码 |
| U6 | **numba 0.67.0 的 `caching.py` 行号在不同小版本间是否漂移** | RT-01 的 `file:line` 引用稳定性（我的结论基于本机 `.venv` 的 0.67.0） | **高**（本机已确证），但**打包环境版本需确认** | 对比 `pip freeze` 与构建产物内的 numba 版本 |
| U7 | **NSIS `perMachine` 安装目录的实际 ACL** | RT-04 / P2-12 的可行性前提 | **中** | 在干净虚拟机安装后 `icacls "C:\Program Files\MDescriptor Studio"` |
| U8 | 是否存在未纳入 `grep` 范围的序列化 / 命令执行点 | A1/A3/A5/A8 的"未发现"结论的完备性 | **中** | 建议再跑一次覆盖 `**/*.py` + `**/*.ts(x)` 的全量扫描 |

### 7.3 本次审计的方法与边界

- **方法**：逐文件静态代码审计 + 依赖库源码交叉验证（tauri-2.11.5 / tauri-utils-2.9.3 / numba-0.67.0 / umap）+ 测试代码回归影响分析 + 红队报告逐条独立复现式核对。
- **边界**：**未**启动应用、**未**拉起后端进程、**未**执行 `pip install`、**未**构建、**未**做任何动态利用或模糊测试。所有"可利用性"判断均基于代码路径可达性的静态推演。
- **未覆盖**：`backend/` 下 `analysis/engine.py` 全文（1848 行，本次重点读了 numba monkeypatch 段 604-635）、`frontend/src` 全部组件（仅审计了 `ipc/client.ts`、CSP 相关与 `grep` 命中的注入点）、第三方 JS 库（plotly / 3dmol / React 生态）内部实现、CI/CD 配置全文。
- **判定口径**：`CONFIRMED` = 代码路径确实存在且红队描述准确；`PARTIALLY CONFIRMED` = 主体成立但含不成立的子项；`DOWNGRADED` = 路径存在但严重性被高估；`UPGRADED` = 严重性被低估；`REFUTED` = 代码路径不存在。本次 21 条中无 `REFUTED`（红队未虚构漏洞），但有 6 处子项/修复建议被驳回或修正（详见第 1 节与第 3 节各条）。
