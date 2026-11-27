<#
.SYNOPSIS
    Automated clean-environment verification for the built Windows setup.exe installer.
    Installs silently into an isolated directory, strips dev tools (Python/Node/Docker/Git) from PATH,
    verifies secure first-run bootstrap, DuckDB queries, CSV upload through the bundled runtime,
    and verifies uninstall preserves user data.
#>
[CmdletBinding()]
param(
    [string]$InstallerPath = "",
    [string]$InstallDir = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$TestsDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$WinSetupDir = Split-Path -Parent $TestsDir
if (-not $InstallerPath) { $InstallerPath = Join-Path $WinSetupDir "dist\setup.exe" }
if (-not $InstallDir) { $InstallDir = Join-Path $env:TEMP ("NexusBI_CleanVerify_" + [Guid]::NewGuid().ToString("N").Substring(0, 8)) }
$TestDataRoot = Join-Path $env:LOCALAPPDATA ("NexusBI_VerifyData_" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
$DataDir = Join-Path $TestDataRoot "data"

if (-not (Test-Path $InstallerPath)) {
    throw "Installer not found at $InstallerPath. Run winsetup\build.ps1 first."
}

$InstallerInfo = Get-Item $InstallerPath
$InstallerSha = (Get-FileHash -Path $InstallerPath -Algorithm SHA256).Hash
Write-Host "Installer: $($InstallerInfo.FullName) ($([Math]::Round($InstallerInfo.Length / 1MB, 2)) MB, SHA256=$InstallerSha)"

Write-Host "=== 1. Installing setup.exe silently into $InstallDir ==="
$proc = Start-Process -FilePath $InstallerPath -ArgumentList "/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES", "/DIR=`"$InstallDir`"" -Wait -PassThru
if ($proc.ExitCode -ne 0) {
    throw "Installer exited with code $($proc.ExitCode)"
}

foreach ($required in @("NexusBI.exe", "runtime\python\python.exe", "runtime\node\node.exe", "runtime\web_gateway.mjs", "backend\app\main.py", "frontend\.next\standalone\server.js", "unins000.exe")) {
    $p = Join-Path $InstallDir $required
    if (-not (Test-Path $p)) {
        throw "Missing expected installed file: $p"
    }
}

Write-Host "=== 2. Launching bundled backend + frontend gateway with stripped PATH (no global Python/Node/Docker) ==="
$origPath = $env:PATH
$backendProc = $null
$gatewayProc = $null
$beOut = Join-Path $TestDataRoot "backend_out.log"
$beErr = Join-Path $TestDataRoot "backend_err.log"
$gwOut = Join-Path $TestDataRoot "gateway_out.log"
$gwErr = Join-Path $TestDataRoot "gateway_err.log"

$savedEnvVars = @{}
$tempEnvNames = @(
    "ENVIRONMENT", "BI_DATA_DIR", "BI_SEED_DEMO_USERS", "BI_REQUIRE_GENERATED_SECRETS",
    "BI_JWT_SECRET", "BI_CRED_KEY", "BI_INITIAL_SETUP_TOKEN", "BI_DESKTOP_CONTROL_TOKEN",
    "PYTHONNOUSERSITE", "PYTHONDONTWRITEBYTECODE",
    "NODE_ENV", "HOSTNAME", "PORT", "NEXT_INTERNAL_PORT", "BACKEND_INTERNAL_URL",
    "STANDALONE_DIR", "GATEWAY_CONTROL_TOKEN"
)
foreach ($k in $tempEnvNames) {
    $savedEnvVars[$k] = [Environment]::GetEnvironmentVariable($k, "Process")
}

try {
    New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"

    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $b1 = New-Object byte[] 48; $rng.GetBytes($b1)
    $b2 = New-Object byte[] 32; $rng.GetBytes($b2)
    $b3 = New-Object byte[] 24; $rng.GetBytes($b3)
    $jwtSecret = [Convert]::ToBase64String($b1).Replace("+", "-").Replace("/", "_").TrimEnd("=")
    $credKey = [Convert]::ToBase64String($b2)
    $setupTok = [Convert]::ToBase64String($b3).Replace("+", "-").Replace("/", "_").TrimEnd("=")
    $ctrlTok = "ci-control-token-12345"

    $backendPort = 19180
    $nextPort = 19101
    $uiPort = 19100

    $pyExe = Join-Path $InstallDir "runtime\python\python.exe"
    $nodeExe = Join-Path $InstallDir "runtime\node\node.exe"
    $gatewayJs = Join-Path $InstallDir "runtime\web_gateway.mjs"
    $standaloneDir = Join-Path $InstallDir "frontend\.next\standalone"

    # Negative check: production without generated secrets MUST fail
    $badPsi = New-Object System.Diagnostics.ProcessStartInfo
    $badPsi.FileName = $pyExe
    $badPsi.Arguments = "-c `"from app.config import settings; settings.validate_runtime_security()`""
    $badPsi.WorkingDirectory = (Join-Path $InstallDir "backend")
    $badPsi.UseShellExecute = $false
    $badPsi.RedirectStandardError = $true
    $badPsi.EnvironmentVariables["ENVIRONMENT"] = "production"
    $badPsi.EnvironmentVariables["BI_DATA_DIR"] = $DataDir
    $badPsi.EnvironmentVariables["BI_REQUIRE_GENERATED_SECRETS"] = "1"
    $badPsi.EnvironmentVariables["PYTHONNOUSERSITE"] = "1"
    $badPsi.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"] = "1"
    $badProc = [System.Diagnostics.Process]::Start($badPsi)
    $badErr = $badProc.StandardError.ReadToEnd()
    $badProc.WaitForExit()
    if ($badProc.ExitCode -eq 0) {
        throw "Security check failed: backend allowed startup without generated secrets!"
    }
    Write-Host "[ok] Refused startup with default dev secrets in production mode."

    # Run Alembic database migrations prior to starting production backend
    $migPsi = New-Object System.Diagnostics.ProcessStartInfo
    $migPsi.FileName = $pyExe
    $migPsi.Arguments = "-m app.db_migrate"
    $migPsi.WorkingDirectory = (Join-Path $InstallDir "backend")
    $migPsi.UseShellExecute = $false
    $migPsi.RedirectStandardOutput = $true
    $migPsi.RedirectStandardError = $true
    $migPsi.EnvironmentVariables["ENVIRONMENT"] = "production"
    $migPsi.EnvironmentVariables["BI_DATA_DIR"] = $DataDir
    $migPsi.EnvironmentVariables["BI_SEED_DEMO_USERS"] = "0"
    $migPsi.EnvironmentVariables["BI_REQUIRE_GENERATED_SECRETS"] = "1"
    $migPsi.EnvironmentVariables["BI_JWT_SECRET"] = $jwtSecret
    $migPsi.EnvironmentVariables["BI_CRED_KEY"] = $credKey
    $migPsi.EnvironmentVariables["PYTHONNOUSERSITE"] = "1"
    $migPsi.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"] = "1"
    $migProc = [System.Diagnostics.Process]::Start($migPsi)
    $migOut = $migProc.StandardOutput.ReadToEnd() + "`n" + $migProc.StandardError.ReadToEnd()
    $migProc.WaitForExit()
    if ($migProc.ExitCode -ne 0) {
        throw "Bundled Alembic migration failed: $migOut"
    }
    Write-Host "[ok] Bundled Alembic migration completed cleanly."

    # Start real bundled backend + gateway with file-redirected logs (no pipe deadlock)
    $env:ENVIRONMENT = "production"
    $env:BI_DATA_DIR = $DataDir
    $env:BI_SEED_DEMO_USERS = "0"
    $env:BI_REQUIRE_GENERATED_SECRETS = "1"
    $env:BI_JWT_SECRET = $jwtSecret
    $env:BI_CRED_KEY = $credKey
    $env:BI_INITIAL_SETUP_TOKEN = $setupTok
    $env:BI_DESKTOP_CONTROL_TOKEN = $ctrlTok
    $env:PYTHONNOUSERSITE = "1"
    $env:PYTHONDONTWRITEBYTECODE = "1"
    $backendProc = Start-Process -FilePath $pyExe `
        -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$backendPort") `
        -WorkingDirectory (Join-Path $InstallDir "backend") `
        -RedirectStandardOutput $beOut -RedirectStandardError $beErr `
        -NoNewWindow -PassThru

    $env:NODE_ENV = "production"
    $env:HOSTNAME = "127.0.0.1"
    $env:PORT = "$uiPort"
    $env:NEXT_INTERNAL_PORT = "$nextPort"
    $env:BACKEND_INTERNAL_URL = "http://127.0.0.1:$backendPort"
    $env:STANDALONE_DIR = $standaloneDir
    $env:GATEWAY_CONTROL_TOKEN = $ctrlTok
    $gatewayProc = Start-Process -FilePath $nodeExe `
        -ArgumentList @("`"$gatewayJs`"") `
        -WorkingDirectory $standaloneDir `
        -RedirectStandardOutput $gwOut -RedirectStandardError $gwErr `
        -NoNewWindow -PassThru

    $baseUrl = "http://127.0.0.1:$uiPort"
    $healthy = $false
    for ($i = 0; $i -lt 90; $i++) {
        if ($backendProc.HasExited) { throw "Bundled backend exited early with code $($backendProc.ExitCode)" }
        if ($gatewayProc.HasExited) { throw "Bundled web gateway exited early with code $($gatewayProc.ExitCode)" }
        try {
            $r1 = Invoke-WebRequest -Uri "$baseUrl/__gateway/health" -UseBasicParsing -TimeoutSec 2
            $r2 = Invoke-WebRequest -Uri "$baseUrl/api/v1/health" -UseBasicParsing -TimeoutSec 2
            if ($r1.StatusCode -eq 200 -and $r2.StatusCode -eq 200) { $healthy = $true; break }
        } catch {}
        Start-Sleep -Milliseconds 500
    }
    if (-not $healthy) { throw "Bundled services did not become healthy" }

    Write-Host "=== 3. Verifying UI, secure initial setup, DuckDB execution, and CSV upload ==="
    foreach ($uiRoute in @("/", "/admin/security", "/admin/monitoring", "/dashboards", "/settings")) {
        $pageResp = Invoke-WebRequest -Uri "$baseUrl$uiRoute" -UseBasicParsing -TimeoutSec 5
        if ($pageResp.StatusCode -ne 200) { throw "Route $uiRoute returned $($pageResp.StatusCode)" }
    }

    $statusJson = Invoke-RestMethod -Uri "$baseUrl/api/v1/auth/status" -Method GET
    if (-not $statusJson.needs_initial_setup -or $statusJson.demo_mode) {
        throw "Expected needs_initial_setup=true and demo_mode=false, got: $($statusJson | ConvertTo-Json -Compress)"
    }

    # Verify default admin account is NOT active
    $defaultLoginBlocked = $false
    try {
        Invoke-RestMethod -Uri "$baseUrl/api/v1/auth/login" -Method POST -ContentType "application/json" `
            -Body '{"username":"admin","password":"Admin!2026"}' | Out-Null
    } catch {
        $defaultLoginBlocked = $true
    }
    if (-not $defaultLoginBlocked) { throw "Default admin password should NOT exist!" }

    # Bootstrap custom admin
    $bootBody = @{
        username = "ci_admin"
        password = "StrongCiPassword!2026"
        email = "ci_admin@local.test"
        full_name = "CI Administrator"
        organization_name = "Clean Verify Org"
        include_sample_data = $true
        setup_token = $setupTok
    } | ConvertTo-Json
    $bootResp = Invoke-RestMethod -Uri "$baseUrl/api/v1/auth/bootstrap-admin" -Method POST -ContentType "application/json" -Headers @{ "X-Auth-Mode" = "bearer" } -Body $bootBody
    $tok = $bootResp.access_token
    if (-not $tok) { throw "Missing access_token from bootstrap-admin" }

    $headers = @{ Authorization = "Bearer $tok" }

    $rbacUsers = Invoke-RestMethod -Uri "$baseUrl/api/v1/rbac/users" -Headers $headers -Method GET
    if (@($rbacUsers).Count -lt 1) { throw "Expected at least 1 RBAC user" }
    $rbacRoles = Invoke-RestMethod -Uri "$baseUrl/api/v1/rbac/roles" -Headers $headers -Method GET
    if (-not $rbacRoles.roles -or @($rbacRoles.roles).Count -lt 5) { throw "Expected RBAC roles payload with roles array" }
    $rbacGroups = Invoke-RestMethod -Uri "$baseUrl/api/v1/rbac/groups" -Headers $headers -Method GET
    if ($null -eq $rbacGroups) { throw "Expected RBAC groups response" }
    $rbacMatrix = Invoke-RestMethod -Uri "$baseUrl/api/v1/rbac/permissions/matrix" -Headers $headers -Method GET
    if (-not $rbacMatrix.dashboards -or @($rbacMatrix.dashboards).Count -lt 1) { throw "Expected RBAC permission matrix dashboards" }
    Write-Host "[ok] Verified Users, Roles, Groups, and Permission Matrix APIs through bundled gateway."

    $rawDashes = Invoke-RestMethod -Uri "$baseUrl/api/v1/dashboards" -Headers $headers -Method GET
    $dashes = [System.Collections.Generic.List[object]]::new()
    foreach ($item in $rawDashes) {
        if ($item -is [System.Array]) {
            foreach ($sub in $item) { $dashes.Add($sub) }
        } else {
            $dashes.Add($item)
        }
    }
    if ($dashes.Count -lt 1) { throw "Expected seeded sample dashboards" }

    $dashId = $dashes[0].id
    $execRes = Invoke-RestMethod -Uri "$baseUrl/api/v1/dashboards/$dashId/execute-batch" -Headers $headers -Method POST -ContentType "application/json" -Body "{}"
    $execWidgetCount = @($execRes.widgets.PSObject.Properties).Count
    if ($execWidgetCount -lt 1) { throw "Expected widget execution results from DuckDB, got $execWidgetCount" }

    $bpmnDash = $dashes | Where-Object { $_.title -like "*BPMN*" } | Select-Object -First 1
    if (-not $bpmnDash) { throw "Expected seeded BPMN Bottleneck dashboard" }
    $bpmnExec = Invoke-RestMethod -Uri "$baseUrl/api/v1/dashboards/$($bpmnDash.id)/execute-batch" -Headers $headers -Method POST -ContentType "application/json" -Body "{}"
    $bpmnWidgetCount = @($bpmnExec.widgets.PSObject.Properties).Count
    if ($bpmnWidgetCount -lt 4) { throw "Expected BPMN dashboard widgets execution results, got $bpmnWidgetCount" }
    Write-Host "[ok] Verified BPMN Bottleneck dashboard execution ($bpmnWidgetCount widgets)."

    # Upload CSV through web_gateway -> FastAPI -> DuckDB
    $tmpCsv = Join-Path $TestDataRoot "ci_sample.csv"
    Set-Content -Path $tmpCsv -Value "region,product,sales`nNorth,WidgetA,1500`nSouth,WidgetB,2900" -Encoding UTF8
    $impOut = & curl.exe -sS -f -X POST "$baseUrl/api/v1/data-sources/files/import" `
        -H "Authorization: Bearer $tok" `
        -F "dataset_name=Windows CI CSV" `
        -F "file=@$tmpCsv;type=text/csv"
    if ($LASTEXITCODE -ne 0) { throw "CSV file upload via bundled web gateway failed" }
    $impJson = $impOut | ConvertFrom-Json
    if ($impJson.row_count -ne 2) { throw "Expected 2 imported CSV rows, got $($impJson.row_count)" }
    Write-Host "[ok] Verified CSV file upload via bundled web gateway."

    # Shutdown gracefully
    Invoke-RestMethod -Uri "$baseUrl/__gateway/shutdown" -Method POST -Headers @{ "X-Gateway-Token" = $ctrlTok } | Out-Null
    Invoke-RestMethod -Uri "http://127.0.0.1:$backendPort/_internal/shutdown" -Method POST -Headers @{ "X-Desktop-Control-Token" = $ctrlTok } | Out-Null
    Start-Sleep -Seconds 2
    if ($gatewayProc -and -not $gatewayProc.HasExited) { & taskkill /PID $gatewayProc.Id /T /F 2>$null | Out-Null }
    if ($backendProc -and -not $backendProc.HasExited) { & taskkill /PID $backendProc.Id /T /F 2>$null | Out-Null }

    if (-not (Test-Path (Join-Path $DataDir "app_metadata.db"))) { throw "SQLite DB missing in BI_DATA_DIR" }
    if (-not (Test-Path (Join-Path $DataDir "warehouse.duckdb"))) { throw "DuckDB warehouse missing in BI_DATA_DIR" }
    if (@(Get-ChildItem -Path (Join-Path $DataDir "uploads") -File -Recurse).Count -lt 1) { throw "Uploaded CSV missing in BI_DATA_DIR\uploads" }

    Write-Host "=== 4. Running silent uninstall and verifying user data is preserved ==="
    $unins = Join-Path $InstallDir "unins000.exe"
    $uProc = Start-Process -FilePath $unins -ArgumentList "/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES" -Wait -PassThru
    if ($uProc.ExitCode -ne 0) { throw "Uninstaller failed with exit code $($uProc.ExitCode)" }

    if (Test-Path (Join-Path $InstallDir "NexusBI.exe")) {
        throw "Uninstall did not remove NexusBI.exe from $InstallDir"
    }
    if (-not (Test-Path (Join-Path $DataDir "app_metadata.db")) -or -not (Test-Path (Join-Path $DataDir "warehouse.duckdb"))) {
        throw "Uninstall accidentally deleted preserved user databases!"
    }
}
catch {
    $errLine = $_.InvocationInfo.ScriptLineNumber
    $errMsg = $_.Exception.Message
    $beTail = if (Test-Path $beErr) { ((Get-Content $beErr -Tail 25 -ErrorAction SilentlyContinue) -join " | ") } else { "" }
    $beOutTail = if (Test-Path $beOut) { ((Get-Content $beOut -Tail 15 -ErrorAction SilentlyContinue) -join " | ") } else { "" }
    $gwTail = if (Test-Path $gwErr) { ((Get-Content $gwErr -Tail 20 -ErrorAction SilentlyContinue) -join " | ") } else { "" }
    $summary = ("verify_windows_bundle failed at line {0}: {1} || beErr: {2} || beOut: {3} || gwErr: {4}" -f $errLine, $errMsg, $beTail, $beOutTail, $gwTail) -replace "[\r\n]+", " "
    if ($summary.Length -gt 3500) { $summary = $summary.Substring(0, 3500) }
    Write-Host "::error::$summary"
    throw
}
finally {
    $env:PATH = $origPath
    foreach ($k in $tempEnvNames) {
        [Environment]::SetEnvironmentVariable($k, $savedEnvVars[$k], "Process")
    }
    if ($gatewayProc -and -not $gatewayProc.HasExited) { & taskkill /PID $gatewayProc.Id /T /F 2>$null | Out-Null }
    if ($backendProc -and -not $backendProc.HasExited) { & taskkill /PID $backendProc.Id /T /F 2>$null | Out-Null }
}

Remove-Item -Path $TestDataRoot -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "ALL WINDOWS INSTALLER VERIFICATION CHECKS PASSED!"
