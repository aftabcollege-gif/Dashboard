[CmdletBinding()]
param(
    [switch]$IncludeCache
)

$ErrorActionPreference = "Stop"
$WinSetupDir = Split-Path -Parent $MyInvocation.MyCommand.Path

foreach ($rel in @("stage", "dist")) {
    $p = Join-Path $WinSetupDir $rel
    if (Test-Path $p) {
        Remove-Item -Path $p -Recurse -Force
        Write-Host "Removed $p"
    }
}

if ($IncludeCache) {
    $cache = Join-Path $WinSetupDir ".cache"
    if (Test-Path $cache) {
        Remove-Item -Path $cache -Recurse -Force
        Write-Host "Removed $cache"
    }
}
