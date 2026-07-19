# setup_venv.ps1
# Create and populate the project virtual environment for powershell-terminal-mcp.
# Plain ASCII. No curl. Run from the project root: .\setup_venv.ps1

$ErrorActionPreference = "Stop"

$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptPath

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "powershell-terminal-mcp - venv setup" -ForegroundColor White
Write-Host "Project Root: $scriptPath" -ForegroundColor White
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host ""

# A pre-existing .venv may be stale (wrong Python version, or missing pywinpty).
if (Test-Path ".venv") {
    Write-Host "Existing .venv found." -ForegroundColor Yellow
    $answer = Read-Host "Delete and recreate it? (y/n)"
    if ($answer -eq "y") {
        Write-Host "Removing old .venv..." -ForegroundColor Yellow
        Remove-Item -Recurse -Force ".venv"
    } else {
        Write-Host "Keeping existing .venv. Aborting to avoid a mixed environment." -ForegroundColor Red
        exit 1
    }
}

Write-Host "Creating .venv with Python 3.11..." -ForegroundColor Yellow
py -3.11 -m venv .venv

$pip = ".\.venv\Scripts\pip.exe"

Write-Host "Upgrading pip..." -ForegroundColor Yellow
& $pip install --upgrade pip

Write-Host "Installing runtime dependencies (requirements.txt)..." -ForegroundColor Yellow
& $pip install -r requirements.txt

Write-Host "Installing project in editable mode (pip install -e .)..." -ForegroundColor Yellow
& $pip install -e .

Write-Host ""
Write-Host "======================================================================" -ForegroundColor Green
Write-Host "venv ready." -ForegroundColor Green
Write-Host "Activate it with:  .\.venv\Scripts\Activate.ps1" -ForegroundColor White
Write-Host "======================================================================" -ForegroundColor Green
