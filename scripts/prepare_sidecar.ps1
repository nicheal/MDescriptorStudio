# Prepare the verified Python backend bundle used by both local and GitHub release builds.
# Produces:
#   src-tauri\resources\backend\                        PyInstaller onedir bundle
#   src-tauri\resources\backend\backend-dependencies.txt  exact installed package versions
#   src-tauri\resources\backend\backend-manifest.json   per-file size + SHA-256
#   src-tauri\binaries\backend-bundle.sha256            SHA-256 of the manifest (embedded at compile time)

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

$bundleDir = "$root\src-tauri\resources\backend"
if (Test-Path -LiteralPath $bundleDir) {
    Remove-Item -LiteralPath $bundleDir -Recurse -Force
}
New-Item -ItemType Directory -Path $bundleDir -Force | Out-Null
Copy-Item "$root\backend\dist\backend\*" $bundleDir -Recurse -Force

# ADR-2 installs the latest PyPI engine at release time, so the versions that were
# actually frozen into this bundle are recorded next to the manifest they ship with.
$freezeFile = Join-Path $bundleDir "backend-dependencies.txt"
& "$root\.venv\Scripts\python.exe" -m pip freeze | Set-Content -LiteralPath $freezeFile -Encoding ascii
if ($LASTEXITCODE -ne 0) { throw "could not record bundled Python package versions" }

# Manifest of every bundled file (sorted relative paths, forward slashes) so the
# Rust shell can verify the bundle against the hash compiled into the binary.
$files = Get-ChildItem -LiteralPath $bundleDir -Recurse -File |
    Sort-Object -Property FullName
$entries = @(foreach ($file in $files) {
    $relative = $file.FullName.Substring($bundleDir.Length + 1).Replace('\', '/')
    if ($relative -eq 'backend-manifest.json') { continue }
    $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    [ordered]@{ path = $relative; size = $file.Length; sha256 = $hash }
})
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$manifestPath = Join-Path $bundleDir "backend-manifest.json"
[IO.File]::WriteAllText($manifestPath, ($entries | ConvertTo-Json -Depth 3), $utf8NoBom)

$manifestHash = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath "$root\src-tauri\binaries\backend-bundle.sha256" `
    -Value "$manifestHash  backend-manifest.json" -Encoding ascii

$totalMB = ($files | Measure-Object Length -Sum).Sum / 1MB -as [int]
Write-Host "Prepared verified backend bundle: $bundleDir ($totalMB MB)"
