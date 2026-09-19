# Build the native statistics geometry kernel (backend/native/mds_native.cpp)
# into the package:  backend/mdescriptor_studio_backend/datasets/_native/mds_native.dll
#
# The DLL is optional at runtime (statistics fall back to the scipy reference
# implementation without it), but package.ps1 runs this script so release
# builds ship the accelerated path.  Uses MinGW g++ when present, otherwise
# MSVC cl.exe from a detected Visual Studio installation.
#
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\build_native.ps1 [-Require]
#
#   -Require  fail instead of shipping/verifying a bundle without the kernel.
#             Release packaging and CI pass this: a silent fallback to scipy is
#             how a missing compiler turns into an unaccelerated app, and the
#             scipy path materialises every periodic image at once, so it needs
#             orders of magnitude more memory on a large frame than the kernel.

param([switch]$Require)

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
} elseif ($Require) {
    throw "no compiler found (g++ or cl.exe) while one is required: the bundle would ship without the native kernel"
} else {
    Write-Warning "No compiler found (g++ or cl.exe); native kernel not rebuilt. Statistics will use the scipy fallback."
    exit 0
}

if (-not (Test-Path -LiteralPath $outDll)) { throw "build reported success but $outDll is missing" }
# A stale DLL from a previous run must not pass for the current source: the
# Python and C++ neighbour searches have to agree, and they are parity-tested.
if ((Get-Item -LiteralPath $outDll).LastWriteTime -lt (Get-Item -LiteralPath $src).LastWriteTime) {
    throw "$outDll is older than $src; the kernel was not actually rebuilt"
}

Write-Host "Built $outDll"
