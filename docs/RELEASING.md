# MDescriptor Studio 发布与应用内升级

当前发布目标为 Windows x64 NSIS 安装包。应用内升级由 Tauri Updater 完成，升级包包含 Studio、Rust 壳、Python backend sidecar 和固定版本的 MDescriptor；用户无需执行 `pip` 命令。

## 一次性配置签名

更新签名私钥不能提交到 Git。正式发布前生成并安全保存一把生产密钥：

```powershell
npm run tauri signer generate -- --ci --write-keys .tauri\mdescriptor-studio.key
```

本地打包脚本会读取以下被 `.gitignore` 忽略的文件：

- `.tauri\mdescriptor-studio.key`
- `.tauri\mdescriptor-studio.key.password`

请把生成密钥时设置的密码保存到第二个文件，或仅在当前终端设置 `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`；不要把密码写入仓库。

CI 则设置 `TAURI_SIGNING_PRIVATE_KEY` 和 `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` 两个 Secret，并将 `src-tauri/tauri.conf.json` 中的 `pubkey` 与这把私钥对应的公钥保持一致。私钥丢失后，已安装旧版本的用户无法验证新更新。

## 发布新版本

1. 将同一个版本号更新到 `src-tauri/tauri.conf.json`、`src-tauri/Cargo.toml`、`frontend/package.json`、`frontend/package-lock.json` 和 `backend/mdescriptor_studio_backend/__init__.py`。
2. 使用生产密钥运行：

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\package.ps1
   ```

3. 在 GitHub 创建对应的 tag，例如 `v0.2.0`，并上传 `src-tauri\target\release\bundle\nsis\` 下的：

   - `MDescriptor Studio_<version>_x64-setup.exe`
   - `MDescriptor Studio_<version>_x64-setup.exe.sig`
   - `latest.json`

   `latest.json` 中的下载地址已由脚本指向该 GitHub Release。发布前确认 tag、文件名和版本号完全一致。

## 用户升级

用户打开“设置 → 应用更新”，点击“检查更新”；发现新版本后点击“安装并重启”。Windows 安装阶段应用会关闭并自动重启，数据目录和已有数据保持不变。

更新配置遵循 [Tauri Updater 文档](https://v2.tauri.app/plugin/updater/)；Windows 安装包仍建议使用代码签名，以减少 SmartScreen 警告，参见 [Tauri Windows 签名文档](https://v2.tauri.app/distribute/sign/windows/)。
