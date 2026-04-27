# GEOai - Local Dev Starter
# Usage: .\start.ps1          (port 8000)
#        .\start.ps1 -Port 9000

param(
    [int]$Port = 8000
)

# Kill old processes on the same port
$oldPids = netstat -ano |
    Select-String ":$Port\s" |
    Select-String "LISTENING" |
    ForEach-Object { ($_ -split '\s+')[-1] } |
    Sort-Object -Unique

if ($oldPids) {
    Write-Host "Clearing old processes on port $Port ..." -ForegroundColor Yellow
    $oldPids | ForEach-Object {
        Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Milliseconds 600
}

# Load .env if present
$envFile = Join-Path $PSScriptRoot "backend\.env"
if (Test-Path $envFile) {
    Get-Content $envFile |
        Where-Object { $_ -match '^\s*[^#]' -and $_ -match '=' } |
        ForEach-Object {
            $parts = $_ -split '=', 2
            [System.Environment]::SetEnvironmentVariable(
                $parts[0].Trim(), $parts[1].Trim(), "Process"
            )
        }
    Write-Host "Loaded .env" -ForegroundColor Cyan
}

# Start server
Write-Host ""
Write-Host "  GEOai Server  http://127.0.0.1:$Port" -ForegroundColor Green
Write-Host "  LIFF           http://127.0.0.1:$Port/liff/" -ForegroundColor Cyan
Write-Host "  Health         http://127.0.0.1:$Port/health" -ForegroundColor Cyan
Write-Host ""

Set-Location "$PSScriptRoot\backend"
python -m uvicorn main:app --host 127.0.0.1 --port $Port --reload
