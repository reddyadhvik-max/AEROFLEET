# ===========================================
#  AEROFLEET V2 - START SCRIPT
# ===========================================

Write-Host ""
Write-Host "  ======================================" -ForegroundColor Cyan
Write-Host "      AEROFLEET V2 - MODULAR PIPELINE   " -ForegroundColor Cyan
Write-Host "      Driver Monitoring System          " -ForegroundColor DarkCyan
Write-Host "  ======================================" -ForegroundColor Cyan
Write-Host ""

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Check Python
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Host "[ERROR] Python not found. Please install Python 3.10+." -ForegroundColor Red
    exit 1
}

Write-Host "[1/3] Checking dependencies..." -ForegroundColor Yellow

# Check if key packages are installed
$missingPkgs = @()
$packages = @("cv2", "mediapipe", "fastapi", "uvicorn", "psycopg2")
foreach ($pkg in $packages) {
    $result = python -c "import $pkg" 2>&1
    if ($LASTEXITCODE -ne 0) {
        $missingPkgs += $pkg
    }
}

if ($missingPkgs.Count -gt 0) {
    Write-Host "[1/3] Installing missing packages..." -ForegroundColor Yellow
    pip install -r "$scriptDir\requirements.txt" --quiet
}

Write-Host "[2/3] Dependencies OK" -ForegroundColor Green

# Set environment variables
$env:TRUCK_ID = "TRK-001"

Write-Host "[3/3] Starting Pipeline + Dashboard..." -ForegroundColor Yellow
Write-Host ""
Write-Host "  Dashboard: http://localhost:8000" -ForegroundColor Cyan
Write-Host "  Login:     admin / aerofleet2025" -ForegroundColor DarkCyan
Write-Host "  API Docs:  http://localhost:8000/docs" -ForegroundColor DarkCyan
Write-Host ""

Set-Location $scriptDir
python main.py
