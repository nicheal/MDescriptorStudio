# Full release packaging for MDescriptor Studio (M5).
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\package.ps1
# Steps:
#   1. Native statistics kernel (optional; best-effort)
#   2. PyInstaller backend sidecar (onefile)
#   3. Copy to src-tauri\binaries\backend-x86_64-pc-windows-msvc.exe
#   4. tauri build  ->  NSIS installer + signed updater bundle
#   5. Generate latest.json for the configured GitHub Releases endpoint

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$localSigningKey = Join-Path $root ".tauri\mdescriptor-studio.key"
$localSigningPassword = Join-Path $root ".tauri\mdescriptor-studio.key.password"
if ([string]::IsNullOrWhiteSpace($env:TAURI_SIGNING_PRIVATE_KEY)) {
    if (Test-Path -LiteralPath $localSigningKey) {
        $env:TAURI_SIGNING_PRIVATE_KEY = $localSigningKey
    } else {
        throw "TAURI_SIGNING_PRIVATE_KEY is required; generate a key with 'npm run tauri signer generate -- --ci --write-keys .tauri\\mdescriptor-studio.key' or provide the CI secret."
    }
}
if ([string]::IsNullOrWhiteSpace($env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD) -and (Test-Path -LiteralPath $localSigningPassword)) {
    $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = (Get-Content -LiteralPath $localSigningPassword -Raw).Trim()
}
if ([string]::IsNullOrWhiteSpace($env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD)) {
    throw "TAURI_SIGNING_PRIVATE_KEY_PASSWORD is required for the encrypted updater key"
}

& powershell -ExecutionPolicy Bypass -File "$root\scripts\build_native.ps1"
if ($LASTEXITCODE -ne 0) { throw "native kernel build failed" }

Push-Location "$root\backend"
& "$root\.venv\Scripts\python.exe" -m PyInstaller backend.spec --noconfirm --log-level ERROR
Pop-Location
if ($LASTEXITCODE -ne 0) { throw "PyInstaller backend build failed" }

Copy-Item "$root\backend\dist\backend.exe" "$root\src-tauri\binaries\backend-x86_64-pc-windows-msvc.exe" -Force
$sidecar = "$root\src-tauri\binaries\backend-x86_64-pc-windows-msvc.exe"
$sidecarHash = (Get-FileHash -LiteralPath $sidecar -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath "$sidecar.sha256" -Value "$sidecarHash  $(Split-Path -Leaf $sidecar)" -Encoding ascii

Push-Location $root
& node "$root\node_modules\@tauri-apps\cli\tauri.js" build
Pop-Location
if ($LASTEXITCODE -ne 0) { throw "Tauri release build failed" }

$config = Get-Content -LiteralPath "$root\src-tauri\tauri.conf.json" -Raw | ConvertFrom-Json
$version = [string]$config.version
$manifestVersion = $version -replace '^v', ''
$releaseTag = if ($version.StartsWith("v")) { $version } else { "v$version" }
$bundleDir = Join-Path $root "src-tauri\target\release\bundle\nsis"
$updateBundle = Get-ChildItem -LiteralPath $bundleDir -Filter "*-setup.exe" -File |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if (-not $updateBundle) { throw "Tauri updater bundle (*-setup.exe) was not generated" }
$signaturePath = "$($updateBundle.FullName).sig"
if (-not (Test-Path -LiteralPath $signaturePath)) { throw "Tauri updater signature was not generated: $signaturePath" }
$manifest = [ordered]@{
    version = $manifestVersion
    notes = "MDescriptor Studio $manifestVersion"
    pub_date = (Get-Date).ToUniversalTime().ToString("o")
    platforms = [ordered]@{
        "windows-x86_64" = [ordered]@{
            url = "https://github.com/nicheal/MDescriptorStudio/releases/download/$releaseTag/$([Uri]::EscapeDataString($updateBundle.Name))"
            signature = (Get-Content -LiteralPath $signaturePath -Raw).Trim()
        }
    }
}
$manifestPath = Join-Path $bundleDir "latest.json"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText($manifestPath, ($manifest | ConvertTo-Json -Depth 6), $utf8NoBom)

Write-Host "Done. Installer:"
Get-ChildItem "$root\src-tauri\target\release\bundle\nsis\*.exe" | ForEach-Object { $_.FullName }
Write-Host "Updater manifest: $manifestPath"
