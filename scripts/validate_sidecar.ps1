# Validate the actual PyInstaller sidecar before a Windows release is built.
# This deliberately talks to the frozen executable over stdio: importing the
# source package is not evidence that the bundled DLLs and dynamic imports work.

[CmdletBinding()]
param(
    [string]$BundleDir,
    [ValidateRange(5, 300)]
    [int]$TimeoutSeconds = 60
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($BundleDir)) {
    $BundleDir = Join-Path $root "src-tauri\resources\backend"
}

$bundlePath = (Resolve-Path -LiteralPath $BundleDir -ErrorAction Stop).Path
$exePath = Join-Path $bundlePath "backend.exe"
$manifestPath = Join-Path $bundlePath "backend-manifest.json"
$manifestHashPath = Join-Path $root "src-tauri\binaries\backend-bundle.sha256"
foreach ($requiredPath in @($exePath, $manifestPath, $manifestHashPath)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "sidecar validation input is missing: $requiredPath"
    }
}

# Check the two artifacts that the Rust launcher verifies before starting the
# process. This catches a stale or partially copied bundle before Tauri build.
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$backendEntry = @($manifest | Where-Object { $_.path -eq "backend.exe" })
if ($backendEntry.Count -ne 1) {
    throw "backend-manifest.json does not contain exactly one backend.exe entry"
}
$backendFile = Get-Item -LiteralPath $exePath
$backendHash = (Get-FileHash -LiteralPath $exePath -Algorithm SHA256).Hash.ToLowerInvariant()
if ([int64]($backendEntry[0].size) -ne [int64]($backendFile.Length) -or
    [string]($backendEntry[0].sha256) -ne $backendHash) {
    throw "backend.exe does not match backend-manifest.json"
}
$manifestHash = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
$recordedHashLine = (Get-Content -LiteralPath $manifestHashPath | Select-Object -First 1).Trim()
$recordedManifestHash = ($recordedHashLine -split "\s+", 2)[0].ToLowerInvariant()
if ($recordedManifestHash -ne $manifestHash) {
    throw "backend-bundle.sha256 does not match backend-manifest.json"
}

$smokeData = Join-Path ([IO.Path]::GetTempPath()) ("mdescriptor-sidecar-smoke-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $smokeData -Force | Out-Null
$process = $null
try {
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $exePath
    $startInfo.WorkingDirectory = $bundlePath
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardInput = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.EnvironmentVariables["MDS_DATA_DIR"] = $smokeData

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    if (-not $process.Start()) {
        throw "could not start frozen backend: $exePath"
    }

    function Read-SidecarLine([System.Diagnostics.Process]$Child, [int]$TimeoutMs) {
        $read = $Child.StandardOutput.ReadLineAsync()
        if (-not $read.Wait($TimeoutMs)) {
            throw "frozen backend produced no response within $([math]::Round($TimeoutMs / 1000))s"
        }
        $line = $read.Result
        if ($null -eq $line) {
            throw "frozen backend closed stdout unexpectedly (exit code $($Child.ExitCode))"
        }
        try {
            return ($line | ConvertFrom-Json)
        } catch {
            throw "frozen backend emitted invalid JSON: $line"
        }
    }

    $timeoutMs = $TimeoutSeconds * 1000
    $ready = Read-SidecarLine $process $timeoutMs
    if ($ready.event -ne "backend.ready" -or [int]$ready.protocol_version -ne 1) {
        throw "frozen backend handshake is invalid"
    }
    if ([string]::IsNullOrWhiteSpace([string]$ready.data.backend_version) -or
        [string]::IsNullOrWhiteSpace([string]$ready.data.analysis_algorithm_version)) {
        throw "frozen backend handshake omitted version metadata"
    }

    function Send-SidecarRequest([System.Diagnostics.Process]$Child, [int]$Id, [string]$Method) {
        $request = [ordered]@{
            protocol_version = 1
            id = $Id
            method = $Method
            params = [ordered]@{}
        } | ConvertTo-Json -Compress
        $Child.StandardInput.WriteLine($request)
        $Child.StandardInput.Flush()
        return (Read-SidecarLine $Child $timeoutMs)
    }

    $info = Send-SidecarRequest $process 1 "system.info"
    if ($info.id -ne 1 -or $null -eq $info.result -or
        [int]$info.result.protocol_version -ne 1 -or
        [string]::IsNullOrWhiteSpace([string]$info.result.mdescriptor_version)) {
        throw "frozen backend system.info response is invalid"
    }

    # These read-only calls force the frozen registry, SQLite setup, and
    # dynamic analysis method wiring to execute after the real warmup gate.
    $analysisList = Send-SidecarRequest $process 2 "analysis.list"
    if ($analysisList.id -ne 2 -or $null -eq $analysisList.result -or
        $analysisList.result -isnot [array]) {
        throw "frozen backend analysis.list response is invalid"
    }
    $datasetList = Send-SidecarRequest $process 3 "dataset.list"
    if ($datasetList.id -ne 3 -or $null -eq $datasetList.result -or
        $datasetList.result -isnot [array] -or $datasetList.result.Count -ne 0) {
        throw "frozen backend dataset.list response is invalid"
    }
    Write-Host "Frozen sidecar smoke check passed: $exePath"
} finally {
    if ($null -ne $process) {
        try {
            if (-not $process.HasExited) {
                $process.StandardInput.Close()
                if (-not $process.WaitForExit(5000)) {
                    $process.Kill()
                    $process.WaitForExit(5000)
                }
            }
        } catch {
            Write-Warning "could not shut down frozen sidecar cleanly: $($_.Exception.Message)"
        } finally {
            $process.Dispose()
        }
    }
    if (Test-Path -LiteralPath $smokeData) {
        Remove-Item -LiteralPath $smokeData -Recurse -Force
    }
}
