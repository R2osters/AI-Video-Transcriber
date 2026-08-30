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

# Verification du bundle : on lance l'exe en mode selftest (sqlite3 + WAL,
# backend.library, app FastAPI, faster_whisper, yt_dlp). L'exe etant windowed,
# sa sortie va dans <AVT_DATA_DIR>\backend.log -- on isole donc les donnees de
# test dans un dossier temporaire pour ne pas polluer celles de l'utilisateur.
Write-Host ">> Verification du bundle (selftest)..."
$TestData = Join-Path $env:TEMP ("avt-selftest-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $TestData | Out-Null
$Exe = Join-Path $Desktop "dist-backend\AVT-Backend\AVT-Backend.exe"

$env:AVT_SELFTEST = "1"
$env:AVT_DATA_DIR = $TestData
try {
    $Proc = Start-Process -FilePath $Exe -Wait -PassThru -NoNewWindow
    $Code = $Proc.ExitCode
} finally {
    Remove-Item Env:\AVT_SELFTEST -ErrorAction SilentlyContinue
    Remove-Item Env:\AVT_DATA_DIR -ErrorAction SilentlyContinue
}

$TestLog = Join-Path $TestData "backend.log"
if ($Code -ne 0) {
    if (Test-Path $TestLog) { Get-Content $TestLog -Tail 40 | Write-Host }
    throw "Selftest du backend echoue ($Code). Detail : $TestLog"
}
if (Test-Path $TestLog) { Get-Content $TestLog -Tail 10 | Write-Host }
try { Remove-Item -Recurse -Force $TestData -ErrorAction Stop } catch {}

Write-Host ">> OK : dist-backend\AVT-Backend\AVT-Backend.exe"
