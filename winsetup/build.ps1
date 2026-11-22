<#
.SYNOPSIS
    Reproducible Windows x64 Offline Installer Builder for NexusBI Enterprise.
    Builds frontend standalone, stages clean backend, bundles relocatable Python 3.11 + wheels
    and Node.js LTS x64, compiles native NexusBI.exe supervisor, and outputs winsetup/dist/setup.exe.

.PARAMETER SkipInstaller
    Stages the full portable runtime & compiles NexusBI.exe without running Inno Setup (ISCC.exe).

.PARAMETER SkipDownloadVcRedist
    Skips downloading vc_redist.x64.exe (useful if only testing non-admin portable DLL bundling).
#>
[CmdletBinding()]
param(
    [switch]$SkipInstaller,
    [switch]$SkipDownloadVcRedist,
    [string]$PythonVersion = "3.11.9",
    [string]$NodeVersion = "22.14.0"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not [Environment]::Is64BitOperatingSystem) {
    throw "Building the Windows x64 installer requires a 64-bit Windows host."
}

$WinSetupDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $WinSetupDir
$FrontendDir = Join-Path $RepoRoot "frontend"
$BackendDir = Join-Path $RepoRoot "backend"
$StageDir = Join-Path $WinSetupDir "stage"
$DistDir = Join-Path $WinSetupDir "dist"
$CacheDir = Join-Path $WinSetupDir ".cache"

New-Item -ItemType Directory -Force -Path $CacheDir | Out-Null
New-Item -ItemType Directory -Force -Path $DistDir | Out-Null

function Resolve-CscExe {
    $candidates = @(
        (Join-Path $env:SystemRoot "Microsoft.NET\Framework64\v4.0.30319\csc.exe"),
        (Join-Path $env:SystemRoot "Microsoft.NET\Framework\v4.0.30319\csc.exe")
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { return $c }
    }
    throw "Could not find .NET Framework csc.exe compiler."
}

function Resolve-IsccExe {
    $cmd = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:LocalAppData "Programs\Inno Setup 6\ISCC.exe")
    )
    foreach ($c in $candidates) {
        if ($c -and (Test-Path $c)) { return $c }
    }
    return $null
}

function Download-CachedFile {
    param([string]$Url, [string]$OutPath)
    if (Test-Path $OutPath) {
        Write-Host "[cache] Using $OutPath"
        return
    }
    Write-Host "[download] $Url -> $OutPath"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $Url -OutFile $OutPath -UseBasicParsing
}

Write-Host "=== Step 1/7: Cleaning staging directory ==="
if (Test-Path $StageDir) { Remove-Item -Path $StageDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path (Join-Path $StageDir "runtime\python") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $StageDir "runtime\node") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $StageDir "backend") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $StageDir "frontend\.next\standalone") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $StageDir "prereqs") | Out-Null

Write-Host "=== Step 2/7: Building Next.js standalone production frontend ==="
Push-Location $FrontendDir
try {
    if (-not (Get-Command "npm" -ErrorAction SilentlyContinue)) {
        throw "Build host requires Node.js & npm to compile the frontend bundle (end users will NOT need them)."
    }
    if (-not (Test-Path (Join-Path $FrontendDir "node_modules"))) {
        & npm ci
        if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
    }
    $env:NEXT_OUTPUT_MODE = "standalone"
    $env:NEXT_DISABLE_BUILD_REWRITES = "1"
    & npm run build
    if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
}
finally {
    Remove-Item Env:NEXT_OUTPUT_MODE -ErrorAction SilentlyContinue
    Remove-Item Env:NEXT_DISABLE_BUILD_REWRITES -ErrorAction SilentlyContinue
    Pop-Location
}

$StandaloneSrc = Join-Path $FrontendDir ".next\standalone"
$StaticSrc = Join-Path $FrontendDir ".next\static"
$StageStandalone = Join-Path $StageDir "frontend\.next\standalone"
Copy-Item -Path (Join-Path $StandaloneSrc "*") -Destination $StageStandalone -Recurse -Force
$StageStatic = Join-Path $StageStandalone ".next\static"
New-Item -ItemType Directory -Force -Path $StageStatic | Out-Null
Copy-Item -Path (Join-Path $StaticSrc "*") -Destination $StageStatic -Recurse -Force
$PublicSrc = Join-Path $FrontendDir "public"
if (Test-Path $PublicSrc) {
    Copy-Item -Path $PublicSrc -Destination (Join-Path $StageStandalone "public") -Recurse -Force
}

Write-Host "=== Step 3/7: Staging clean backend application (excluding dev data & venv) ==="
Copy-Item -Path (Join-Path $BackendDir "app") -Destination (Join-Path $StageDir "backend\app") -Recurse -Force
Copy-Item -Path (Join-Path $BackendDir "migrations") -Destination (Join-Path $StageDir "backend\migrations") -Recurse -Force
Copy-Item -Path (Join-Path $BackendDir "requirements.txt") -Destination (Join-Path $StageDir "backend\requirements.txt") -Force
Get-ChildItem -Path (Join-Path $StageDir "backend") -Include "__pycache__", "*.pyc", ".pytest_cache" -Recurse -Force |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "=== Step 4/7: Bundling relocatable Python $PythonVersion (win_amd64) & dependencies ==="
$NugetZip = Join-Path $CacheDir "python.$PythonVersion.zip"
Download-CachedFile -Url "https://www.nuget.org/api/v2/package/python/$PythonVersion" -OutPath $NugetZip
$PyExtractTmp = Join-Path $CacheDir "py_extract_$PythonVersion"
if (Test-Path $PyExtractTmp) { Remove-Item -Path $PyExtractTmp -Recurse -Force }
Expand-Archive -Path $NugetZip -DestinationPath $PyExtractTmp -Force
Copy-Item -Path (Join-Path $PyExtractTmp "tools\*") -Destination (Join-Path $StageDir "runtime\python") -Recurse -Force

$BundledPython = Join-Path $StageDir "runtime\python\python.exe"
if (-not (Test-Path $BundledPython)) { throw "Relocatable python.exe not found after extraction" }

# Filter out dev-only test dependencies (pytest) for smaller clean runtime (UTF-8 without BOM)
$RuntimeReqs = Join-Path $StageDir "backend\requirements.runtime.txt"
$FilteredReqs = @(Get-Content (Join-Path $BackendDir "requirements.txt") | Where-Object { $_ -and ($_ -notmatch "^pytest") })
[System.IO.File]::WriteAllLines($RuntimeReqs, $FilteredReqs, (New-Object System.Text.UTF8Encoding $false))

& $BundledPython -m pip install --upgrade pip --no-warn-script-location --no-cache-dir
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }
& $BundledPython -m pip install --no-warn-script-location --no-cache-dir -r $RuntimeReqs
if ($LASTEXITCODE -ne 0) { throw "pip install requirements failed" }

# Smoke-check critical Python C-extension imports inside bundled runtime
& $BundledPython -c "import fastapi, uvicorn, sqlalchemy, duckdb, pandas, openpyxl, reportlab, cryptography, bcrypt, apscheduler, sqlparse, ldap3; print('PYTHON_RUNTIME_OK')"
if ($LASTEXITCODE -ne 0) { throw "Bundled Python dependency smoke check failed" }

# Strip __pycache__ inside site-packages to keep installer compact
Get-ChildItem -Path (Join-Path $StageDir "runtime\python\Lib\site-packages") -Include "__pycache__" -Recurse -Force -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "=== Step 5/7: Bundling portable Node.js $NodeVersion (win-x64) & web_gateway.mjs ==="
$NodeArchiveName = "node-v$NodeVersion-win-x64"
$NodeZip = Join-Path $CacheDir "$NodeArchiveName.zip"
$NodeSums = Join-Path $CacheDir "node-v$NodeVersion-SHASUMS256.txt"
Download-CachedFile -Url "https://nodejs.org/dist/v$NodeVersion/$NodeArchiveName.zip" -OutPath $NodeZip
Download-CachedFile -Url "https://nodejs.org/dist/v$NodeVersion/SHASUMS256.txt" -OutPath $NodeSums

$ExpectedHash = (Get-Content $NodeSums | Where-Object { $_ -match "$NodeArchiveName\.zip$" } | Select-Object -First 1).Split(" ")[0].ToUpper()
$ActualHash = (Get-FileHash -Path $NodeZip -Algorithm SHA256).Hash.ToUpper()
if ($ExpectedHash -ne $ActualHash) {
    throw "Node.js archive SHA256 mismatch! Expected $ExpectedHash, got $ActualHash"
}
Write-Host "[verified] Node.js SHA256: $ActualHash"

$NodeExtractTmp = Join-Path $CacheDir "node_extract_$NodeVersion"
if (Test-Path $NodeExtractTmp) { Remove-Item -Path $NodeExtractTmp -Recurse -Force }
Expand-Archive -Path $NodeZip -DestinationPath $NodeExtractTmp -Force
Copy-Item -Path (Join-Path $NodeExtractTmp "$NodeArchiveName\node.exe") -Destination (Join-Path $StageDir "runtime\node\node.exe") -Force
Copy-Item -Path (Join-Path $NodeExtractTmp "$NodeArchiveName\LICENSE") -Destination (Join-Path $StageDir "runtime\node\LICENSE.txt") -Force
Copy-Item -Path (Join-Path $WinSetupDir "runtime\web_gateway.mjs") -Destination (Join-Path $StageDir "runtime\web_gateway.mjs") -Force

# Bundle VC++ runtime DLLs locally next to python.exe and node.exe so even non-admin installs work cleanly
$PyDir = Join-Path $StageDir "runtime\python"
$NodeDir = Join-Path $StageDir "runtime\node"
foreach ($dll in @("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll")) {
    $sysDll = Join-Path $env:SystemRoot "System32\$dll"
    $pyDll = Join-Path $PyDir $dll
    if ((Test-Path $sysDll) -and (-not (Test-Path $pyDll))) {
        Copy-Item -Path $sysDll -Destination $pyDll -Force
    }
    if (Test-Path $pyDll) {
        Copy-Item -Path $pyDll -Destination (Join-Path $NodeDir $dll) -Force
    }
    elseif (Test-Path $sysDll) {
        Copy-Item -Path $sysDll -Destination (Join-Path $NodeDir $dll) -Force
    }
}

if (-not $SkipDownloadVcRedist) {
    $VcRedist = Join-Path $CacheDir "vc_redist.x64.exe"
    Download-CachedFile -Url "https://aka.ms/vs/17/release/vc_redist.x64.exe" -OutPath $VcRedist
    Copy-Item -Path $VcRedist -Destination (Join-Path $StageDir "prereqs\vc_redist.x64.exe") -Force
}

Write-Host "=== Step 6/7: Compiling native x64 NexusBI.exe supervisor launcher ==="
$CscExe = Resolve-CscExe
$LauncherSrc = Join-Path $WinSetupDir "launcher\Program.cs"
$Manifest = Join-Path $WinSetupDir "launcher\app.manifest"
$LauncherOut = Join-Path $StageDir "NexusBI.exe"
& $CscExe /nologo /target:winexe /platform:x64 /optimize+ "/win32manifest:$Manifest" `
    /r:System.Windows.Forms.dll /r:System.Drawing.dll /r:System.Security.dll `
    "/out:$LauncherOut" $LauncherSrc
if ($LASTEXITCODE -ne 0) { throw "Failed to compile NexusBI.exe launcher" }

Write-Host "=== Step 7/7: Packaging single offline setup.exe via Inno Setup 6 ==="
if ($SkipInstaller) {
    Write-Host "SkipInstaller set; staged artifacts ready at: $StageDir"
    exit 0
}

$IsccExe = Resolve-IsccExe
if (-not $IsccExe) {
    throw "Inno Setup 6 (ISCC.exe) was not found. Install Inno Setup 6 on the Windows build machine (e.g., winget install JRSoftware.InnoSetup) and re-run build.ps1."
}

$IssPath = Join-Path $WinSetupDir "installer\NexusBI.iss"
& $IsccExe $IssPath
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compilation failed" }

$SetupExe = Join-Path $DistDir "setup.exe"
if (-not (Test-Path $SetupExe)) { throw "Expected installer missing at $SetupExe" }
$SetupHash = (Get-FileHash -Path $SetupExe -Algorithm SHA256).Hash
$SizeMB = [Math]::Round((Get-Item $SetupExe).Length / 1MB, 2)
Write-Host ""
Write-Host "SUCCESS: Built standalone Windows x64 installer:"
Write-Host "  Path   : $SetupExe"
Write-Host "  Size   : $SizeMB MB"
Write-Host "  SHA256 : $SetupHash"
