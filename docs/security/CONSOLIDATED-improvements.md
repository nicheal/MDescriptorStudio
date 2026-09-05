# MDescriptor Studio — 红蓝对抗统一改进清单

> 本文是主代理对两份报告的**汇总与裁决**，不是第三轮审计。
> 上游输入：`red-team-findings.md`（红队，21 条）、`blue-team-review.md`（蓝队，复核 21 条 + 补充 14 条 + 整改计划）。
> 用法：**照第 3 节顺序执行即可**；每条都标注了工作量、回归风险与硬依赖。改代码前请先读对应报告条目的详情。

- 项目：Tauri 2 桌面应用（React 前端 + Rust 壳 + Python sidecar，stdio NDJSON IPC）
- 威胁模型：恶意/被污染数据集文件、被攻陷的 npm/PyPI/crates 依赖、同机低权限恶意软件、前端注入脚本
- 生成时间：2026-08-31

---

## 1. 对抗结论对照（裁决结果）

| 维度 | 红队 | 蓝队 | 主代理裁决 |
|---|---|---|---|
| 发现条数 | 21（主清单 13 + 附加 8） | 复核 21 + 新增 14（BLUE-01…14） | **合并去重后 35 条**：红队 21 + 蓝队补充 14（无重叠） |
| 严重度分布 | High 3 / Medium 7 / Low 3 | 补充 14 条中 Medium 4 | **High 3 / Medium 11 / Low 及信息类 21** |
| 结论可信度 | — | CONFIRMED 20 / PARTIALLY CONFIRMED 1 / REFUTED 0 | 采信。红队证据链扎实，蓝队行号复核后仅 2 处轻微偏移 |
| 阴性结论 | SQL 注入、硬编码凭据、"首方无 XSS sink" 三项判为未发现 | 前两项**独立复核成立**；第三项（A8 自研 pickle 解析器）**不予背书** | 采信蓝队。A8 降级为"需 fuzz 验证"，并新增 BLUE-10（`model` 参数零校验） |

### 1.1 最终确认的 P0（3 条，构成完整本地攻击链）

| ID | 问题 | 一句话影响 | 为何是 P0 |
|---|---|---|---|
| **RT-01** | numba JIT 缓存被重定向到 `%TEMP%`/`MDS_DATA_DIR`，numba 在 `caching.py:588` 先 `pickle.load` 后校验 | 同机投放恶意 `.nbi` → 用户跑 UMAP 即 RCE | 无交互、无用户提示、稳定触发 |
| **RT-02** | `analysis.export` 的 `output_path` 仅 `expanduser()`，无任何其他校验 | 任意路径写覆盖 + `mkdir(parents=True)` + UNC 触发 NTLM 外泄 | 一步到位的任意文件写 |
| **RT-03** | `backend_send` 是全权限 IPC 代理：无方法白名单、不拒换行（帧注入）、响应广播可伪造 | 任一被污染的前端依赖即可放大成任意文件写 + RCE | 所有 (b)/(d) 类威胁的**放大器** |

### 1.2 双方分歧点与我采纳的结论（5 处）

| # | 分歧 | 裁决 |
|---|---|---|
| 1 | **RT-03 修复建议"在 capabilities 声明 `backend_send` 权限"** —— 蓝队核实 tauri-2.11.5 源码后判定此建议**无效** | **采纳蓝队**。应用自定义命令仅在存在 `__app-acl__` manifest 或来源为远程时才做 ACL 校验，本项目无该 manifest → 加了也是假安全。正解见 P0-1（先摘掉 `core:event` 的 emit 权限，这是零成本止血） |
| 2 | **RT-09 的"8 MB 限制不可绕过"** —— 蓝队证伪：`for raw in sys.stdin` 先整行读入内存才检查长度 | **采纳蓝队**。8 MB 只约束**调度**，不约束**内存**。已并入 P1-1 |
| 3 | **RT-05 的"release 每次启动都请求 PyPI"** —— 蓝队证伪：`_frozen()` 在 `start_check` 开头即 return | **采纳蓝队**。release 实际不发请求，RT-05 影响面收敛到 dev 构建，维持 Medium |
| 4 | **RT-02 修复方案** —— 红队建议限制到 `data_dir/exports` 白名单 | **采纳蓝队否决意见**。该白名单会破坏 2 个在跑测试（`test_analysis_ipc.py:169-172`、`test_analysis_api.py:300`）并打断"导出到 D 盘"的合法需求。改用 `resolve()` + `is_relative_to()` + 拒绝清单 |
| 5 | **RT-07 指纹算法变更** —— 红队未评估迁移与性能 | **采纳蓝队**。直接改会让全部历史 run 被标 STALE（破坏性数据迁移），且 `compute_fingerprint` 在 RPC 同步路径上被调用，`dataset.list` 一次读 4 MiB×N 会卡死 UI。**必须先出迁移方案再改代码** |

---

## 2. 合并后的发现总表（35 条）

> 定级为蓝队复核后的**最终定级**。`R`=红队，`B`=蓝队补充。

| ID | 标题 | 最终定级 | CWE | 位置 |
|---|---|---|---|---|
| RT-01 | numba JIT 缓存投毒 → pickle RCE | **High**（共享 `MDS_DATA_DIR` 时 Critical） | CWE-502/427 | `analysis/engine.py:604-635` |
| RT-02 | `analysis.export` 的 `output_path` 无校验 → 任意路径写 | **High** | CWE-22/73/497 | `services/analysis_service.py:669-673, 1335-1357` |
| RT-03 | `backend_send` 全权限 IPC 代理 + 帧注入 + 响应伪造 | **High** | CWE-345/93/807 | `src-tauri/src/main.rs:22-35,86-96` |
| RT-04 | Release 从 exe 同目录加载 sidecar，无完整性校验 | Medium | CWE-427/426 | `main.rs:106-126`；`backend/backend.spec:55-68` |
| RT-05 | pip 自更新信任 PyPI 版本号，未锁索引/哈希 | Medium | CWE-494/829 | `services/update_service.py:73-116` |
| RT-06 | `dataset.register` 任意路径解析（`format` 可绕过扩展名检查） | Medium | CWE-22/209/73 | `services/dataset_service.py:128-140`；`datasets/base.py:69-84` |
| RT-07 | 数据集指纹只哈希 size+mtime，不含内容 | Medium | CWE-354/345 | `datasets/fingerprint.py:9-22` |
| RT-08 | extxyz `natoms` 无上限 + 全量载帧 → OOM DoS | Medium | CWE-789/400 | `datasets/extxyz.py:38-50,99-100` |
| RT-09 | RPC 线程池任务队列无界 → 内存放大 DoS | Medium | CWE-770/400 | `protocol/server.py:25,41-47` |
| RT-10 | DB 中的 `result_path` 无条件 `rmtree` → 任意目录删除 | Medium | CWE-22/59 | `services/result_service.py:105-113`；`analysis_service.py:378-381,1570-1575` |
| BLUE-01 | 协议版本不匹配时 `os._exit(2)` 直接杀死整个后端 | Medium | CWE-703/754 | `protocol/server.py:51-60` |
| BLUE-02 | 后端 stdin 按行读取无长度上限（8 MB 检查在读取之后） | Medium | CWE-400/770 | `protocol/server.py:41` |
| BLUE-03 | `dataset.frame` 是**任意文件读取 oracle**（DB 投毒路径，不需要 webview 代码执行） | Medium | CWE-22/200 | `services/dataset_service.py:113-121,376-388` |
| BLUE-04 | 子进程环境未清洗：`PYTHONPATH`/`PIP_*`/`TEMP` 全部继承 | Medium | CWE-426/427 | `main.rs:106-138`；`update_service.py:101-110` |
| RT-11 | `MDS_DATA_DIR` 完全受控无校验（RT-01/RT-10 的放大器） | Low | CWE-15/426 | `config.py:12-21`；`main.rs:117-121` |
| RT-12 | 供应链：依赖无哈希锁定、npm 全 `^`、165 MB 二进制入库无校验和 | Low | CWE-494/1357 | `requirements.txt`；`frontend/package.json`；`scripts/package.ps1` |
| RT-13 | CSP 缺 `object-src`/`base-uri`/`frame-ancestors`，保留 `style-src 'unsafe-inline'` | Low | CWE-1021/693 | `src-tauri/tauri.conf.json:25` |
| BLUE-05 | `JobService` 队列同样无界，且 `submit` 先写 DB 行 | Low–Medium | CWE-770 | `services/job_service.py:86,99-116` |
| BLUE-06 | `_artifact_is_complete` 信任磁盘 `manifest.json` 的 `path` | Low | CWE-345 | `analysis_service.py:1467-1474` |
| BLUE-07 | `os.replace(staging, final)` 未考虑 `final` 为悬空 junction | Low | CWE-59 | `analysis_service.py:1271-1316` |
| BLUE-08 | 产物数组名只替换 `/`，未处理 `\` 与 `..` | Low | CWE-22 | `analysis_service.py:1290` |
| BLUE-09 | SQL `LIKE` 通配符未转义 → 过滤器可被 `%`/`_` 绕过 | Low | CWE-89 | `analysis_service.py:355-356,1552-1553`；`dataset_service.py:371` |
| BLUE-10 | `descriptor.submit` 的 `model` 参数零路径校验 + 回显完整路径 | Low | CWE-22/209 | `services/descriptor_service.py:235-294,373` |
| BLUE-11 | `Database.query/query_one` 不受写锁保护（健壮性） | Low | CWE-662 | `storage/database.py:106-148` |
| BLUE-12 | 指纹算法变更会导致全量历史 run 标 STALE（**变更风险**） | 信息 | — | `datasets/fingerprint.py` |
| BLUE-13 | `data_dir()` 被两处独立调用，校验只加一处会不一致 | Low | CWE-1188 | `config.py`；`logging_setup.py:16-17` |
| BLUE-14 | stdio 用 `errors="strict"`，单个畸形字节即终止后端 | Low | CWE-703 | `main.py:33` |
| A1 | 2 处 `np.load` 未显式 `allow_pickle=False`（跨模块不一致） | Low | CWE-502 | `services/result_service.py:77,142` |
| A2 | `job.cancel` 无归属/权限校验 | Low | CWE-862 | `main.py:104`；`job_service.py:208-219` |
| A3 | `settings.set` 无 key 白名单与长度限制 | Low | CWE-770 | `main.py:74-79`；`database.py:77-80` |
| A4 | 错误信息与日志泄露绝对路径与内部异常 | Low | CWE-209 | `protocol/server.py:72-76`；`logging_setup.py:17,22-32` |
| A5 | 产物写入用 staging + `os.replace`（**缓解有效**，但暴露 BLUE-06/07/08） | 信息 + 3 Low | — | `analysis_service.py:1271-1316` |
| A6 | SQL 注入 —— **未发现**（阴性结论成立） | 无 | — | 全部 `execute/query` 均参数化 |
| A7 | 硬编码凭据 —— **未发现**（阴性结论成立） | 无 | — | 独立 grep 仅命中无关标识符 |
| A8 | mdescriptor 自研 pickle 解析器 —— **蓝队未背书，建议 fuzz** | 信息 | — | `.venv/.../mdescriptor/weights.py` |

---

## 3. 统一改进清单（按执行顺序）

### P0 — 立即修复（6 项，关闭 3 条完整攻击链）

- [ ] **P0-1 · RT-03（第 1 步）· 5 分钟 · 零风险**
      `src-tauri/capabilities/default.json`：把 `"core:event:default"` 换成 `"core:event:allow-listen"` + `"core:event:allow-unlisten"`。
      → 一键关闭整条响应伪造链。已确认前端未使用 `emit`。**建议第一个做。**
- [ ] **P0-2 · RT-03（第 2 步）· S · 低风险**
      `src-tauri/src/main.rs:22-35` 在 `backend_send` 开头加两个早退分支：`line.bytes().any(|b| b == b'\n' || b == b'\r' || b == 0)` 与 `line.len() > 8MB`。
- [ ] **P0-3 · RT-11 + BLUE-13 · S · 低风险 · ⚠ 先于 P0-4**
      `MDS_DATA_DIR` 校验：拒绝 UNC / 相对路径 / `\\?\` / `\\.\` / ADS；`config.py:20` 建目录后校验 ACL（非当前用户可写则告警）；`main.rs:117` 加 `cfg!(debug_assertions)` 守卫（release 不透传）。
      `logging_setup.py:17` 与 `main.py:153` **统一改为调用同一个已校验入口**（否则只改一处，另一处照旧）。
- [ ] **P0-4 · RT-01 · S · 中风险**
      numba 缓存根脱离 `MDS_DATA_DIR`/`%TEMP%`，固定到 `%LOCALAPPDATA%\MDescriptorStudio\numba-cache`；
      在 `backend/run_backend.py` **顶部**（import numba 之前）设 `os.environ["NUMBA_DISABLE_JIT_CACHE"]="1"`。
      **必须先实测 UMAP 首跑耗时**（禁用缓存会 +20~40 s 首次编译）再决定是否永久禁用。
- [ ] **P0-5 · RT-02（后端）· M · 中风险 · ⚠ 在 P0-2 之后**
      `analysis_service.py:669-673` 与 `_write_export:1318` **两处都**加 `output_path` 校验：
      拒绝 UNC / `\\?\` / `\\.\` / ADS / 相对路径 / 保留设备名（`CON`/`NUL`/`AUX`）/ 尾部空格与点 / 中间及末端符号链接；用 `Path.resolve()` + `is_relative_to()`；落盘用 `os.open(..., O_CREAT|O_WRONLY|O_TRUNC|O_NOFOLLOW, 0o600)`。
      **不要**用 `".." in resolved.parts`（`resolve()` 后不可能残留 `..`，是死代码）；**不要**限制到 `data_dir/exports`（会破坏 2 个测试与合法需求）。
- [ ] **P0-6 · RT-02（前端）· S · 低风险**
      `frontend/src/pages/Analysis.tsx:216,764,770,914`：导出路径只能由 `plugin-dialog` 的 `save()` 填充，禁止手输。dialog 插件已授权，可与 P0-1 同期做。

### P1 — 本迭代（8 项）

- [ ] **P1-1 · RT-09 + BLUE-02 + BLUE-05 + BLUE-14 · M · 中风险 · ⚠ 先于 P1-4**
      ① RPC 池用 `BoundedSemaphore(64)` 做背压（**不要自己写线程池**，红队的 `_BoundedPool` 会在 shutdown 挂死）；
      ② `server.py:41` 改 `sys.stdin.buffer.readline(MAX_LINE_BYTES+1)` + 丢弃到 EOL + `errors="replace"`；
      ③ `JobService` 同样加背压，且 `submit` 把"写 DB 行"移到获取信号量**之后**。
      限流后前端需处理 `BUSY`（`client.ts` 加重试/提示）。
- [ ] **P1-2 · RT-08 · M · 低风险**
      `extxyz.py:39` 后加 `natoms` 上限（1e6）与"文件剩余字节 < natoms×16 视为损坏"；`:99` 分配前估算字节数、设 512 MiB 上限；`_parse_comment` 的 `except OSError` 扩为 `(OSError, ValueError)` 并转 `AppError(INVALID_DATASET)`；`descriptor_service.py:336-343` 改分块。
- [ ] **P1-3 · RT-10 + BLUE-03 · S · 低风险 · ⚠ 在 P0-3 之后**
      删除路径**不信任 DB**：`result.remove`/`analysis.delete` 改为从 `run_id`/`analysis_id` 重新推导受信任路径，与 DB 值比对，不一致则 `log.error` + 拒绝删除。`_rmtree_quiet` 拆成严格版（带 `onerror` 记录）与宽松版。`dataset.frame:378` 加读取侧校验 + `not isinstance(index, bool)`。
- [ ] **P1-4 · RT-07 + BLUE-12 · L · 高风险 · ⚠ 最晚做，必须先出迁移方案**
      指纹改为 "size + mtime_ns + 头/中/尾各 1 MiB 采样"，带 `v2:` 版本前缀；加进程内 TTL 缓存与全局采样预算（32 MiB）；旧指纹数据集后台 rescan，期间标 `FINGERPRINT_MIGRATING` **不标 STALE**。
- [ ] **P1-5 · A4 + BLUE-03/10（脱敏）· M · 低风险 · 建议尽早**
      给 `AppError` 加 `public_message` 字段，`server.py:75` 只发 `public_message` + `error_id`；逐一改 11 处回显点；`MODEL_NOT_FOUND` 只回 basename。
      已核实**所有测试只断言 `error.code`，不断言消息文本** → 零测试回归。
- [ ] **P1-6 · RT-06 · S~M · 低风险 · 在 P0-2 之后**
      `dataset_service.py:136` 改为 `fmt = detect_format(path)`（删掉 `format` 参数）；`register` 入口加 UNC / 相对 / ADS / 保留设备名 / 尾部空格与点检查；`fingerprint.py:13` 的 `rglob` 加条目与总字节上限。已核实无测试给 `register` 传 `format`。
- [ ] **P1-7 · BLUE-01 · S · 低风险**
      协议版本不匹配只回错误帧**不退出**；若确需终止用 `self._closed.set()` + `self.close()`。`main.rs:89` 的 ready 检测改解析 JSON 判断 `event=="backend.ready"`（现在是字符串 `contains`，脆弱）。
- [ ] **P1-8 · BLUE-04 · S~M · 中风险**
      子进程环境清洗：`main.rs` 的 dev 与 release 两个分支都 `.env_remove("PYTHONPATH"/"PYTHONSTARTUP"/"PYTHONHOME"/"PIP_*")`；`update_service.py:101` 传 `env=clean_env`。
      ⚠ Windows 上**不要**用 `env_clear()`（会破坏 DLL 解析），必须黑名单 `env_remove`，改完实测启动。

### P2 — 后续迭代（15 项，摘要）

| 编号 | 涉及 ID | 动作 | 工作量 |
|---|---|---|---|
| P2-1 | RT-03（第 3 步） | `backend_send` → `backend_request(method, params)`：Rust 用 CSPRNG 生成 id 并序列化，事件改 `emit_to(webview_label)`；同步改 `client.ts:66-82` **与** `preview.tsx:844` 的 mock | L |
| P2-2 | RT-04 + RT-12 | `scripts/package.ps1` 加 `Get-FileHash` 写 `.sha256`；`main.rs` 用 `include_str!` 内嵌哈希比对（**两个候选名都要校验**）；165 MB 二进制移出 Git | M~L |
| P2-3 | RT-05 | 版本号正则（`_check:80` 与 `update_runner:96` 两处）；Popen 加 `--index-url https://pypi.org/simple --only-binary=:all: --no-cache-dir --no-input`（跳过 `--require-hashes`） | S |
| P2-4 | RT-13 | CSP 保守收紧：只加 `object-src 'none'; base-uri 'none'; frame-ancestors 'none'`，显式补 `http://tauri.localhost`，**`script-src` 本轮不动**（避免拦掉 plotly/3dmol 的 blob Worker）；三页实测后再单独收紧 | XS |
| P2-5 | A1 + A3 | `np.load` 显式 `allow_pickle=False`；全仓复查 `pickle.load`/`yaml.load`/`torch.load`/`joblib.load`，`yaml.load` 一律换 `safe_load`；`settings.set` 加 key 白名单与长度限制 | S |
| P2-6 | BLUE-06/07/08 | 产物落盘三项：`_artifact_is_complete` 用重推导路径比对磁盘 manifest；`_commit_artifact` 落盘前 `os.lstat` 命中 `S_IFLNK` 先 `os.unlink`；`safe_name` 同时替换 `\`、拒绝 `.`/`..`、NFKC 归一、截断 64 字符 | S~M |
| P2-7 | BLUE-11 | 推荐 `threading.local()` 每线程一连接（避免读被写阻塞）；退而求其次才纳入写锁 | S |
| P2-8 | BLUE-09 | 新增 `escape_like(s)`，三处调用点补 `ESCAPE '\'` | XS |
| P2-9 | A2 | `job.cancel` 增加调用来源与 job 创建者绑定（依赖 P2-1 引入的 `webview` 参数） | S |
| P2-10 | BLUE-10 | `descriptor.submit` 的 `model` 参数**复用 P0-5 的校验函数**，或收敛到"模型目录白名单根"（用户自定义模型路径是合法需求，不要硬限制到 `data_dir`） | S |
| P2-11 | RT-12 | `pip-compile --generate-hashes` + `--require-hashes`；`package.json` 的 13 处 `^` 改精确版本，CI 强制 `npm ci`；`Cargo.lock` 入仓 + `cargo audit` | M |
| P2-12 | RT-04 | 实测确认 `%PROGRAMFILES%\MDescriptor Studio` 普通用户不可写；sidecar 启动前 `SetDllDirectory("")`；`binaries` 目录禁非管理员写 | M |
| P2-13 | A8 | 前端净化全量复查（`dangerouslySetInnerHTML`/`innerHTML`/`new Function`/`eval(`/`javascript:`）；`StructurePreview` 的 3dmol/plotly 数据渲染前过 JSON schema 校验；`preview.tsx` 的 mock 尽量复用 `client.ts` 序列化逻辑 | M |
| P2-14 | BLUE-13 | `data_dir()` 单点收敛为带缓存的 `data_dir_validated()`（**随 P0-3 一起做，不要拆两次**） | S |
| P2-15 | BLUE-14 + A6/A7 | 新增 `tests/test_protocol_fuzz.py`：超长行、无换行结尾、非法 UTF-8、`vid` 为 `bool`/`str`/`dict`/`list`/`null`、`method` 非串、`params` 为数组、JSON 嵌套 1000 层、单行 8 MB+1。**⚠ 必须在 P1-1 / P1-7 之后** | M |

### P3 — 纵深防御（4 项，不阻断发布）

- [ ] P3-1 · `backend.spec` 的 `console=False`（⚠ 必须先完成 P1-5 日志脱敏，否则把信息泄露从"屏幕"搬到"磁盘"）
- [ ] P3-2 · `database.sqlite` 只落"仅当前用户可写"目录；启动 `PRAGMA integrity_check`；关键行（`datasets.path`/`runs.result_path`/`analysis.artifact_path`）加 HMAC
- [ ] P3-3 · IPC 审计：记录 `method`、耗时、来源窗口（脱敏），异常频次告警
- [ ] P3-4 · 把 B1–B11 信任边界、35 条结论沉淀为 `docs/security/threat-model.md`；每条修复配一条回归用例

### 3.1 修复关键路径与三条硬依赖

```
P0-1（capabilities 去 emit，5 分钟，零风险）
  ↓
P0-2（Rust 侧拒控制字符 + 限长）
  ↓
P0-3（MDS_DATA_DIR 校验 + data_dir 单点收敛 → 含 P2-14）
  ↓
P0-4（numba 缓存根迁出 / 禁用）      ← 依赖 P0-3
  ↓
P0-5 + P0-6（export 路径校验 + 前端 dialog 化）
  ↓
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

1. **P0-3 早于 P0-4**：numba 缓存根要落在 `MDS_DATA_DIR` 之外，得先确定 `MDS_DATA_DIR` 本身可信。
2. **P1-5 早于 P3-1**：`console=False` 之后控制台不可见，日志若仍回显原始路径/异常，问题更难发现。
3. **P1-1 早于 P1-4**：指纹加内容摘要会让 `dataset.list` 变慢，无界队列会把"慢"放大成 OOM。

### 3.2 收敛效果速览

| 档位 | 条数 | 纯配置/零代码 | 修完后 |
|---|---|---|---|
| P0 | 6 | 1（P0-1） | 关闭"无交互 RCE + 任意路径写 + 响应伪造"三条完整攻击链 |
| P1 | 8 | 0 | 关闭信息泄露 oracle、DoS/资源放大、删除路径劫持、协议自杀 |
| P2 | 15 | 2 | 补齐供应链、产物完整性、并发、前端净化、回归用例 |
| P3 | 4 | 1 | 纵深防御，防回退 |

---

## 4. 残余风险与未覆盖

- **未实测项**（报告中的结论均基于代码分析，以下需真机验证）：numba 禁用缓存后的 UMAP 首跑耗时；`%PROGRAMFILES%` 目录普通用户可写性；BLUE-04 的 release 分支环境继承；BLUE-07 的悬空 junction 行为；P2-4 的 CSP 三页实测。
- **未审查项**：`.venv` 内第三方库内部实现（含 `mdescriptor/weights.py` 的自研 pickle 解析器 —— A8 未被背书，建议 fuzz）；`src-tauri/binaries/` 的 165 MB 预构建二进制；编译产物与 `frontend/node_modules`。
- **修完仍存在的结构性风险**：只要 `backend_send` 仍是"字符串透传"模型（P2-1 之前），前端任一依赖被投毒都能直达后端全部方法表 —— P0-1/P0-2 只是提高门槛，不是根治。
