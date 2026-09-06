# Build the native statistics geometry kernel (backend/native/mds_native.cpp)
# into the package:  backend/mdescriptor_studio_backend/datasets/_native/mds_native.dll
#
# The DLL is optional at runtime (statistics fall back to the scipy reference
# implementation without it), but package.ps1 runs this script so release
# builds ship the accelerated path.  Uses MinGW g++ when present, otherwise
# MSVC cl.exe from a detected Visual Studio installation.
#
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\build_native.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$src = Join-Path $root "backend\native\mds_native.cpp"
$outDir = Join-Path $root "backend\mdescriptor_studio_backend\datasets\_native"
$outDll = Join-Path $outDir "mds_native.dll"

if (-not (Test-Path $src)) { throw "source not found: $src" }
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

$gpp = Get-Command g++ -ErrorAction SilentlyContinue
$cl = Get-Command cl -ErrorAction SilentlyContinue

if ($gpp) {
    & $gpp.Source -O3 -std=c++17 -shared -static-libgcc -static-libstdc++ `
        -fno-exceptions -fno-rtti -Wall -Wextra `
        -o $outDll $src
    if ($LASTEXITCODE -ne 0) { throw "g++ build failed" }
} elseif ($cl) {
    & $cl /nologo /O2 /LD /EHsc /Fe:$outDll $src
    if ($LASTEXITCODE -ne 0) { throw "cl.exe build failed" }
} else {
    Write-Warning "No compiler found (g++ or cl.exe); native kernel not rebuilt. Statistics will use the scipy fallback."
    exit 0
}

Write-Host "Built $outDll"
