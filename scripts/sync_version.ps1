[CmdletBinding()]
param(
    [switch]$Check,
    [string]$Tag
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$utf8NoBom = New-Object System.Text.UTF8Encoding -ArgumentList $false
$drift = New-Object 'System.Collections.Generic.List[string]'

function Read-RepoText {
    param([string]$RelativePath)

    $path = Join-Path $root $RelativePath
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required version file is missing: $RelativePath"
    }
    return [IO.File]::ReadAllText($path)
}

function Save-RepoText {
    param(
        [string]$RelativePath,
        [string]$Before,
        [string]$After
    )

    if ($Before -eq $After) {
        return
    }
    if ($Check) {
        [void]$drift.Add($RelativePath)
        return
    }

    [IO.File]::WriteAllText((Join-Path $root $RelativePath), $After, $utf8NoBom)
    Write-Host "Synchronized $RelativePath"
}

function Replace-Once {
    param(
        [string]$Text,
        [string]$Pattern,
        [string]$Replacement,
        [string]$RelativePath
    )

    $regex = [regex]::new($Pattern)
    $count = $regex.Matches($Text).Count
    if ($count -ne 1) {
        throw "Expected exactly one version field in $RelativePath, found $count"
    }
    return $regex.Replace($Text, $Replacement, 1)
}

function Update-TextFile {
    param(
        [string]$RelativePath,
        [string]$Pattern,
        [string]$Replacement
    )

    $before = Read-RepoText $RelativePath
    $after = Replace-Once -Text $before -Pattern $Pattern -Replacement $Replacement -RelativePath $RelativePath
    Save-RepoText -RelativePath $RelativePath -Before $before -After $after
}

function Get-ExactGitTag {
    try {
        $tag = (& git -C $root describe --tags --exact-match --match "v[0-9]*" 2>$null | Select-Object -First 1)
        if (-not [string]::IsNullOrWhiteSpace([string]$tag)) {
            return ([string]$tag).Trim()
        }
    } catch {
        return $null
    }
    return $null
}

if ([string]::IsNullOrWhiteSpace($Tag)) {
    $Tag = Get-ExactGitTag
}

$configRelativePath = "src-tauri\tauri.conf.json"
$configBefore = Read-RepoText $configRelativePath
$config = $configBefore | ConvertFrom-Json
$version = [string]$config.version
$semverPattern = '^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$'

if ($Tag) {
    $Tag = $Tag.Trim()
    if (-not $Tag.StartsWith("v")) {
        throw "Git tag '$Tag' must start with 'v'"
    }
    $version = $Tag.Substring(1)
    if ([string]::IsNullOrWhiteSpace($version) -or $version -notmatch $semverPattern) {
        throw "Git tag '$Tag' does not contain a valid semver version"
    }

    $replacement = '${1}' + $version + '${2}'
    $configAfter = Replace-Once -Text $configBefore `
        -Pattern '(?m)^([ \t]{2}"version"\s*:\s*")[^"]+(\")' `
        -Replacement $replacement -RelativePath $configRelativePath
    Save-RepoText -RelativePath $configRelativePath -Before $configBefore -After $configAfter
} elseif ([string]::IsNullOrWhiteSpace($version) -or $version -notmatch $semverPattern) {
    throw "src-tauri\tauri.conf.json must contain a semver version without a leading 'v'"
}

$replacement = '${1}' + $version + '${2}'
Update-TextFile -RelativePath "src-tauri\Cargo.toml" `
    -Pattern '(?ms)(\A\[package\]\r?\n.*?^version\s*=\s*")[^"]+(\")' `
    -Replacement $replacement

Update-TextFile -RelativePath "frontend\package.json" `
    -Pattern '(?m)^([ \t]{2}"version"\s*:\s*")[^"]+(\")' `
    -Replacement $replacement

$lockRelativePath = "frontend\package-lock.json"
$lockBefore = Read-RepoText $lockRelativePath
$lockAfter = Replace-Once -Text $lockBefore `
    -Pattern '(?m)^([ \t]{2}"version"\s*:\s*")[^"]+(\")' `
    -Replacement $replacement -RelativePath $lockRelativePath
$lockAfter = Replace-Once -Text $lockAfter `
    -Pattern '(?ms)(^[ \t]{2}"packages"\s*:\s*\{\r?\n[ \t]{4}""\s*:\s*\{.*?^[ \t]{6}"version"\s*:\s*")[^"]+(\")' `
    -Replacement $replacement -RelativePath $lockRelativePath
Save-RepoText -RelativePath $lockRelativePath -Before $lockBefore -After $lockAfter

Update-TextFile -RelativePath "src-tauri\Cargo.lock" `
    -Pattern '(?ms)(^\[\[package\]\]\r?\nname\s*=\s*"mdescriptor-studio"\r?\nversion\s*=\s*")[^"]+(\")' `
    -Replacement $replacement

Update-TextFile -RelativePath "backend\mdescriptor_studio_backend\__init__.py" `
    -Pattern '(?m)^(__version__\s*=\s*")[^"]+(\")' `
    -Replacement $replacement

if ($Check) {
    if ($drift.Count -gt 0) {
        $files = ($drift | Sort-Object -Unique) -join ", "
        throw "Version drift detected in: $files. Run scripts\sync_version.ps1."
    }
    Write-Host "Version check passed: $version"
} else {
    if ($Tag) {
        Write-Host "Version synchronized from Git tag '$Tag': $version"
    } else {
        Write-Host "Version synchronized from src-tauri\tauri.conf.json: $version"
    }
}
