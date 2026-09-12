# Prepare the verified Python sidecar used by both local and GitHub release builds.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

& powershell -ExecutionPolicy Bypass -File "$root\scripts\build_native.ps1"
if ($LASTEXITCODE -ne 0) { throw "native kernel build failed" }

Push-Location "$root\backend"
try {
    & "$root\.venv\Scripts\python.exe" -m PyInstaller backend.spec --noconfirm --log-level ERROR
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller backend build failed" }
} finally {
    Pop-Location
}

$sidecar = "$root\src-tauri\binaries\backend-x86_64-pc-windows-msvc.exe"
Copy-Item "$root\backend\dist\backend.exe" $sidecar -Force
$sidecarHash = (Get-FileHash -LiteralPath $sidecar -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath "$sidecar.sha256" -Value "$sidecarHash  $(Split-Path -Leaf $sidecar)" -Encoding ascii

Write-Host "Prepared verified sidecar: $sidecar"
