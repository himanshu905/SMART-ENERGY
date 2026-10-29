$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$python = $null
$uvPythonRoot = Join-Path $env:APPDATA "uv\python"
if (Test-Path -LiteralPath $uvPythonRoot) {
    $runtimes = Get-ChildItem -LiteralPath $uvPythonRoot -Directory |
        Where-Object { $_.Name -like "cpython-*" } |
        Sort-Object Name -Descending
    foreach ($runtime in $runtimes) {
        $candidate = Join-Path $runtime.FullName "python.exe"
        if (Test-Path -LiteralPath $candidate) {
            $python = $candidate
            break
        }
    }
}

if (-not $python) {
    foreach ($commandName in @("py", "python3", "python")) {
        $command = Get-Command $commandName -ErrorAction SilentlyContinue
        if ($command) {
            try {
                $version = & $command.Source --version 2>$null
                if ($LASTEXITCODE -eq 0 -and $version -match "^Python 3\.") {
                    $python = $command.Source
                    break
                }
            } catch {
                continue
            }
        }
    }
}

if (-not $python) {
    Write-Error "Python 3 was not found. Install Python 3 for Windows, reopen this folder, and run run-website.bat again."
    exit 1
}

$url = "http://127.0.0.1:8001"
$healthUrl = "$url/api/setup-status"

try {
    $health = Invoke-RestMethod -Uri $healthUrl -Method Get -TimeoutSec 2
    Write-Host "Wattwise is already running."
    Start-Process $url
    exit 0
} catch {
    if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -ne 404) {
        Write-Error "Port 8001 is being used by another service. Close it and retry run-website.bat."
        exit 1
    }
}

$serverProcess = Start-Process `
    -FilePath $python `
    -ArgumentList @("server.py") `
    -WorkingDirectory $PSScriptRoot `
    -PassThru

for ($attempt = 0; $attempt -lt 40; $attempt++) {
    if ($serverProcess.HasExited) {
        Write-Error "The Wattwise server stopped while starting. Check the Python installation and port 8001."
        exit 1
    }
    try {
        $health = Invoke-RestMethod -Uri $healthUrl -Method Get -TimeoutSec 2
        if ($null -ne $health.setupRequired -and
            $health.adminEmail -eq "divyanshusingh31113@gmail.com" -and
            $health.customerEmail -eq "thehorrorofcentury@gmail.com") {
            Write-Host "Wattwise is ready at $url"
            Start-Process $url
            exit 0
        }
        Write-Error "Port 8001 is not running the Wattwise website. Close that service and retry."
        exit 1
    } catch {
        if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -ne 404) {
            Stop-Process -Id $serverProcess.Id -ErrorAction SilentlyContinue
            Write-Error "Port 8001 is occupied by a different web server. Close it and retry run-website.bat."
            exit 1
        }
        Start-Sleep -Milliseconds 500
    }
}

if (-not $serverProcess.HasExited) {
    Stop-Process -Id $serverProcess.Id -ErrorAction SilentlyContinue
}
Write-Error "The Wattwise server did not start. Check the Python installation and port 8001."
exit 1
