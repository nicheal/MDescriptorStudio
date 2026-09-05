# Full release packaging for MDescriptor Studio (M5).
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\package.ps1
# Steps:
#   1. PyInstaller backend sidecar (onefile)
#   2. Copy to src-tauri\binaries\backend-x86_64-pc-windows-msvc.exe
#   3. tauri build  ->  NSIS setup.exe under src-tauri\target\release\bundle\nsis\

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

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
