# Full release packaging for MDescriptor Studio (M5).
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\package.ps1
# Steps:
#   1. Native statistics kernel (optional; best-effort)
#   2. PyInstaller backend sidecar (onefile)
#   3. Copy to src-tauri\binaries\backend-x86_64-pc-windows-msvc.exe
#   4. tauri build  ->  NSIS setup.exe under src-tauri\target\release\bundle\nsis\

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

& powershell -ExecutionPolicy Bypass -File "$root\scripts\build_native.ps1"
if ($LASTEXITCODE -ne 0) { throw "native kernel build failed" }

Push-Location "$root\backend"
& "$root\.venv\Scripts\python.exe" -m PyInstaller backend.spec --noconfirm --log-level ERROR
Pop-Location

Copy-Item "$root\backend\dist\backend.exe" "$root\src-tauri\binaries\backend-x86_64-pc-windows-msvc.exe" -Force
$sidecar = "$root\src-tauri\binaries\backend-x86_64-pc-windows-msvc.exe"
$sidecarHash = (Get-FileHash -LiteralPath $sidecar -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath "$sidecar.sha256" -Value "$sidecarHash  $(Split-Path -Leaf $sidecar)" -Encoding ascii

Push-Location $root
& node "$root\node_modules\@tauri-apps\cli\tauri.js" build
Pop-Location

Write-Host "Done. Installer:"
Get-ChildItem "$root\src-tauri\target\release\bundle\nsis\*.exe" | ForEach-Object { $_.FullName }
