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
2. 确认仓库已配置 `TAURI_SIGNING_PRIVATE_KEY` 和 `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` 两个 Actions Secret。

3. 创建并推送版本 tag：

   ```powershell
   git tag v0.2.0
   git push origin v0.2.0
   ```

4. `.github/workflows/release.yml` 会在 Windows runner 上构建 sidecar 和 Tauri 安装包，自动创建 Release 并上传安装包、`.sig` 和 `latest.json`。

如需只在本地验证产物，可运行 `scripts\package.ps1`；它会在 `src-tauri\target\release\bundle\nsis\` 生成同样的安装包和更新清单。

`v0.1.0.rc` 这类预发布 tag 只适合验证 CI 构建流程；它不会作为稳定版本被 `releases/latest` 更新地址选中。要验证应用内升级，需要先发布一个较低的稳定版本，再发布更高的稳定版本。

## 用户升级

用户打开“设置 → 应用更新”，点击“检查更新”；发现新版本后点击“安装并重启”。Windows 安装阶段应用会关闭并自动重启，数据目录和已有数据保持不变。

更新配置遵循 [Tauri Updater 文档](https://v2.tauri.app/plugin/updater/)；Windows 安装包仍建议使用代码签名，以减少 SmartScreen 警告，参见 [Tauri Windows 签名文档](https://v2.tauri.app/distribute/sign/windows/)。
