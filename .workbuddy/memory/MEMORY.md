# 项目长期记忆 — MDescriptor Studio (D:\codex\MD)

## 项目形态
Tauri 2 桌面应用（Windows，NSIS perMachine 安装）：React+TS 前端 → Rust 壳（`src-tauri/src/main.rs`，注册 `backend_send`/`backend_restart`/`backend_ready_line`）→ Python sidecar（`backend/mdescriptor_studio_backend/`），三者通过 **stdio NDJSON** 通信。后端 stdout 每行被 Rust 转发为 `backend-message` 事件广播给 webview。

## 安全基线（2026-08-31 红蓝对抗审查结论）
- 已知 3 条 P0 未修复：numba JIT 缓存投毒 RCE、`analysis.export` 的 `output_path` 无校验、`backend_send` 全权限 IPC 代理（无方法白名单、不拒换行、响应可伪造）。
- 完整清单与整改顺序见 `docs/security/CONSOLIDATED-improvements.md`（P0 6 项 / P1 8 项 / P2 15 项 / P3 4 项）。
- 三条硬依赖：先固定并校验 `MDS_DATA_DIR` 再动 numba 缓存根；先做错误消息脱敏再隐藏控制台；先做资源限流再改数据集指纹算法。

## 踩过的坑（写代码时注意）
- Tauri 应用自定义命令默认**不走 ACL 校验**（除非有 `__app-acl__` manifest 或远程来源），给 app 命令加 capability 声明是无效的。
- `for raw in sys.stdin` 会先把整行读进内存，长度限制必须在读取侧（`readline(max_bytes)`）做，不能只在解析侧做。
- `Path.resolve()` 之后不存在 `..` 分量，路径校验要用 `is_relative_to()`，黑名单 `".." in parts` 是死代码。
- Windows 路径校验必须同时覆盖：UNC、`\\?\`、`\\.\`、ADS（`::$DATA`）、保留设备名（CON/NUL/AUX）、尾部空格与点、符号链接/junction。
- 清洗子进程环境在 Windows 上**不要**用 `env_clear()`（破坏 DLL 解析），用黑名单 `env_remove()`。

## 用户偏好
- 安全审查偏好：结构化清单（ID / 严重度 / CWE / 文件:行证据 / 利用场景 / 修复建议），并要求红蓝双方结论比对后形成统一改进清单。
