# Construit le backend en exe autonome (PyInstaller, mode onedir).
# Utilise un venv dedie pour eviter le site-packages global (PyAV casse).
# NOTE: script ASCII pur (PowerShell 5.1 lit les .ps1 sans BOM en ANSI).
$ErrorActionPreference = "Stop"
$Desktop = Split-Path $PSScriptRoot -Parent
Set-Location $Desktop

if (-not (Test-Path ".venv")) {
    Write-Host ">> Creation du venv de build..."
    python -m venv .venv
}

$Py = Join-Path $Desktop ".venv\Scripts\python.exe"

Write-Host ">> Installation des dependances..."
& $Py -m pip install --upgrade pip
& $Py -m pip install -r (Join-Path $Desktop "..\requirements.txt")
& $Py -m pip install pyinstaller

Write-Host ">> Build PyInstaller..."
& $Py -m PyInstaller backend.spec --noconfirm --distpath dist-backend

if ($LASTEXITCODE -ne 0) { throw "PyInstaller a echoue ($LASTEXITCODE)" }
Write-Host ">> OK : dist-backend\AVT-Backend\AVT-Backend.exe"
